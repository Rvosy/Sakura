from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from app.storage.timeline import TimelineDataError, TimelineStore

from .errors import LegacyImportError
from .files import copy_tree_checked
from .history import import_history
from .inspector import detect_legacy_version, legacy_source_is_active
from .transaction import PendingCommit, commit_payload


_TIMELINE_COLUMNS = (
    "entry_id",
    "turn_id",
    "character_id",
    "kind",
    "origin",
    "created_at",
    "payload_json",
)
_MAX_PUBLIC_CONFLICTS = 100
_MAX_PUBLIC_CHARACTERS = 256


@dataclass
class _Counts:
    history_new: int = 0
    history_identical: int = 0
    history_conflicts: int = 0
    memory_new: int = 0
    memory_identical: int = 0
    memory_conflicts: int = 0

    def public(self, character_id: str) -> dict[str, object]:
        return {
            "characterId": character_id[:128],
            "history": {
                "new": self.history_new,
                "identical": self.history_identical,
                "conflicts": self.history_conflicts,
            },
            "memory": {
                "new": self.memory_new,
                "identical": self.memory_identical,
                "conflicts": self.memory_conflicts,
            },
        }


@dataclass
class _Plan:
    source_label: str
    by_character: dict[str, _Counts] = field(default_factory=lambda: defaultdict(_Counts))
    conflicts: list[dict[str, str]] = field(default_factory=list)
    recoverable_errors: int = 0
    blocked: bool = False
    digest_items: list[str] = field(default_factory=list)

    def classify(
        self,
        *,
        domain: str,
        character_id: str,
        item_id: str,
        status: str,
        signature: str,
        hard: bool = False,
    ) -> None:
        counts = self.by_character[character_id]
        field_name = f"{domain}_{status}"
        setattr(counts, field_name, getattr(counts, field_name) + 1)
        self.digest_items.append(f"{domain}\0{character_id}\0{item_id}\0{status}\0{signature}")
        if status == "conflicts":
            if len(self.conflicts) < _MAX_PUBLIC_CONFLICTS:
                self.conflicts.append(
                    {
                        "id": hashlib.sha256(
                            f"{domain}\0{character_id}\0{item_id}".encode()
                        ).hexdigest()[:24],
                        "domain": domain,
                        "characterId": character_id[:128],
                        "itemId": item_id[:128],
                    }
                )
            self.blocked = self.blocked or hard

    def public(self) -> dict[str, object]:
        totals = _Counts()
        for counts in self.by_character.values():
            for name in totals.__dataclass_fields__:
                setattr(totals, name, getattr(totals, name) + getattr(counts, name))
        token_seed = "\n".join(sorted(self.digest_items)).encode()
        token = hashlib.sha256(token_seed).hexdigest()
        ordered_characters = sorted(self.by_character, key=str.casefold)
        return {
            "schemaVersion": 1,
            "planToken": token,
            "sourceLabel": self.source_label,
            "characters": [
                self.by_character[character_id].public(character_id)
                for character_id in ordered_characters[:_MAX_PUBLIC_CHARACTERS]
            ],
            "charactersTruncated": len(ordered_characters) > _MAX_PUBLIC_CHARACTERS,
            "totals": {
                "historyNew": totals.history_new,
                "historyIdentical": totals.history_identical,
                "historyConflicts": totals.history_conflicts,
                "memoryNew": totals.memory_new,
                "memoryIdentical": totals.memory_identical,
                "memoryConflicts": totals.memory_conflicts,
                "recoverableErrors": self.recoverable_errors,
            },
            "conflicts": self.conflicts,
            "requiresConflictConfirmation": bool(self.conflicts),
            "blocked": self.blocked,
        }


@dataclass(frozen=True)
class _ScopeResolution:
    scope: str
    conflict: bool = False


def inspect_character_data_import(source: Path, target: Path) -> dict[str, object]:
    source = Path(source).resolve(strict=True)
    target = Path(target).resolve(strict=True)
    _validate_source(source, target)
    with tempfile.TemporaryDirectory(prefix="sakura-data-import-inspect-") as temporary:
        converted = Path(temporary) / "converted"
        plan = _inspect_into_plan(source, target, converted)
        return plan.public()


def run_character_data_import(
    source: Path,
    target: Path,
    *,
    plan_token: str,
    overwrite_conflicts: bool,
    import_id: str | None = None,
) -> tuple[dict[str, object], PendingCommit]:
    source = Path(source).resolve(strict=True)
    target = Path(target).resolve(strict=True)
    _validate_source(source, target)
    import_id = import_id or uuid.uuid4().hex
    if not import_id.replace("-", "").isalnum():
        raise LegacyImportError("LEGACY_IMPORT_ID_INVALID", "inspect")
    staging = target / f".legacy-import-staging-{import_id}"
    if staging.exists():
        raise LegacyImportError("LEGACY_IMPORT_RECOVERY_REQUIRED", "staging")
    converted = staging / "converted"
    payload = staging / "payload"
    plan = _inspect_into_plan(source, target, converted)
    public_plan = plan.public()
    if public_plan["planToken"] != plan_token:
        shutil.rmtree(staging, ignore_errors=True)
        raise LegacyImportError("LEGACY_DATA_IMPORT_PLAN_STALE", "inspect")
    if plan.blocked:
        shutil.rmtree(staging, ignore_errors=True)
        raise LegacyImportError("LEGACY_DATA_SCOPE_CONFLICT", "inspect")
    if plan.conflicts and not overwrite_conflicts:
        shutil.rmtree(staging, ignore_errors=True)
        raise LegacyImportError("LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED", "inspect")

    try:
        _copy_current_domains(target, payload)
        _merge_timeline(converted, payload, overwrite_conflicts=overwrite_conflicts)
        _merge_memory(
            converted,
            payload,
            overwrite_conflicts=overwrite_conflicts,
            current_scope=_legacy_current_scope(source),
            quarantine=payload
            / "data"
            / "legacy-imports"
            / import_id
            / "quarantine"
            / "memory",
        )
        quarantine = converted / "data" / "legacy-imports" / "incremental-scan"
        if quarantine.is_dir():
            copy_tree_checked(
                quarantine,
                payload / "data" / "legacy-imports" / import_id,
                cancelled=lambda: False,
            )
        report = {
            "schemaVersion": 1,
            "importId": import_id,
            "outcome": "core_validating",
            "plan": public_plan,
        }
        report_path = payload / "data" / "legacy-imports" / import_id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        shutil.rmtree(converted, ignore_errors=True)
        return report, commit_payload(target, import_id, payload)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_source(source: Path, target: Path) -> None:
    history = source / "data" / "chat_history"
    try:
        has_legacy_history = history.is_dir() and any(
            path.is_file() and ".jsonl" in path.name for path in history.iterdir()
        )
    except OSError:
        has_legacy_history = False
    recognized = (
        detect_legacy_version(source).startswith("0.9")
        and (
            has_legacy_history
            or (source / "data" / "memory").is_dir()
        )
    )
    if not recognized:
        raise LegacyImportError("LEGACY_DATA_SOURCE_UNRECOGNIZED", "inspect")
    if legacy_source_is_active(source):
        raise LegacyImportError("LEGACY_SOURCE_ACTIVE", "inspect")
    if source == target or source in target.parents or target in source.parents:
        raise LegacyImportError("LEGACY_SOURCE_TARGET_OVERLAP", "inspect")


def _inspect_into_plan(source: Path, target: Path, converted: Path) -> _Plan:
    character_ids = _discover_scopes(source)
    stats = import_history(
        source,
        converted,
        character_ids=character_ids,
        import_id="incremental-scan",
    )
    plan = _Plan(source.name[:120])
    plan.recoverable_errors += stats.errors_quarantined
    plan.recoverable_errors += _snapshot_source_memory(source, converted)
    _inspect_timeline(converted, target, plan)
    _inspect_memory(converted, target, plan, current_scope=_legacy_current_scope(source))
    return plan


def _snapshot_source_memory(source: Path, converted: Path) -> int:
    """Freeze the 0.9.x Memory domains used by both inspect and apply."""

    source_memory = source / "data/memory"
    frozen = converted / "data/memory"
    errors = 0
    qdrant = source_memory / "qdrant"
    if qdrant.is_dir():
        try:
            copy_tree_checked(
                qdrant,
                frozen / "qdrant",
                cancelled=lambda: False,
                skip_noise=True,
            )
        except Exception:  # noqa: BLE001 - other Memory domains remain salvageable
            errors += 1
            shutil.rmtree(frozen / "qdrant", ignore_errors=True)

    history = source_memory / "mem0_history.db"
    if history.is_file():
        try:
            _sqlite_snapshot(history, frozen / "mem0_history.db")
        except Exception:  # noqa: BLE001 - preserve opaque bytes for quarantine
            copied = False
            for suffix in ("", "-wal", "-shm", "-journal"):
                source_path = Path(f"{history}{suffix}")
                if source_path.is_file():
                    destination = frozen / source_path.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(source_path, destination)
                        copied = True
                    except OSError:
                        errors += 1
            if not copied:
                errors += 1

    profiles = source_memory / "core_profiles.json"
    if profiles.is_file():
        try:
            frozen.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profiles, frozen / profiles.name)
        except OSError:
            errors += 1
    return errors


def _discover_scopes(source: Path) -> tuple[str, ...]:
    scopes: set[str] = set()
    history = source / "data" / "chat_history"
    if history.is_dir():
        for path in history.iterdir():
            if path.is_file() and ".jsonl" in path.name:
                scopes.add(path.name.split(".jsonl", 1)[0])
    characters = source / "characters"
    if characters.is_dir():
        for manifest in characters.glob("*/character.json"):
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            character_id = value.get("id") if isinstance(value, dict) else None
            if isinstance(character_id, str) and character_id.strip():
                scopes.add(character_id.strip())
    current = _legacy_current_scope(source)
    if current:
        scopes.add(current)
    return tuple(sorted(scopes, key=str.casefold))


def _legacy_current_scope(source: Path) -> str:
    path = source / "data" / "config" / "characters.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return ""
    raw = value.get("current_character_id") if isinstance(value, dict) else None
    return str(raw or "").strip()


def _timeline_rows(path: Path) -> dict[str, tuple[str, ...]]:
    if not path.is_file():
        return {}
    TimelineStore(path).assert_activated()
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            f"SELECT {', '.join(_TIMELINE_COLUMNS)} FROM timeline_entries"
        ).fetchall()
    return {str(row[0]): tuple(str(value) for value in row) for row in rows}


def _inspect_timeline(converted: Path, target: Path, plan: _Plan) -> None:
    source_rows = _timeline_rows(converted / "data/chat_history/timeline.sqlite3")
    try:
        target_rows = _timeline_rows(target / "data/chat_history/timeline.sqlite3")
    except (TimelineDataError, sqlite3.Error) as exc:
        raise LegacyImportError(
            "LEGACY_DATA_TARGET_TIMELINE_INVALID", "inspect"
        ) from exc
    target_turns = {row[1]: row[2] for row in target_rows.values()}
    for entry_id, row in source_rows.items():
        character_id = row[2]
        existing = target_rows.get(entry_id)
        hard = bool(existing and existing[2] != character_id) or (
            row[1] in target_turns and target_turns[row[1]] != character_id
        )
        status = "new" if existing is None else ("identical" if existing == row else "conflicts")
        if hard:
            status = "conflicts"
        plan.classify(
            domain="history",
            character_id=character_id,
            item_id=entry_id,
            status=status,
            signature=_signature({"source": row, "target": existing}),
            hard=hard,
        )


def _copy_current_domains(target: Path, payload: Path) -> None:
    for relative in (Path("data/chat_history"), Path("data/memory")):
        source = target / relative
        if source.is_dir():
            copy_tree_checked(source, payload / relative, cancelled=lambda: False)


def _merge_timeline(converted: Path, payload: Path, *, overwrite_conflicts: bool) -> None:
    source_path = converted / "data/chat_history/timeline.sqlite3"
    if not source_path.is_file():
        return
    target_path = payload / "data/chat_history/timeline.sqlite3"
    store = TimelineStore(target_path)
    store.initialize()
    source_rows = _timeline_rows(source_path)
    target_rows = _timeline_rows(target_path)
    target_turns = {row[1]: row[2] for row in target_rows.values()}
    with closing(sqlite3.connect(target_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for entry_id, row in source_rows.items():
            existing = target_rows.get(entry_id)
            if existing == row:
                continue
            if (existing is not None and existing[2] != row[2]) or (
                row[1] in target_turns and target_turns[row[1]] != row[2]
            ):
                raise LegacyImportError("LEGACY_DATA_SCOPE_CONFLICT", "staging")
            if existing is not None and not overwrite_conflicts:
                raise LegacyImportError("LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED", "staging")
            connection.execute(
                f"""
                INSERT INTO timeline_entries ({', '.join(_TIMELINE_COLUMNS)})
                VALUES ({', '.join('?' for _ in _TIMELINE_COLUMNS)})
                ON CONFLICT(entry_id) DO UPDATE SET
                    turn_id=excluded.turn_id,
                    kind=excluded.kind,
                    origin=excluded.origin,
                    created_at=excluded.created_at,
                    payload_json=excluded.payload_json
                """,
                row,
            )
            target_turns[row[1]] = row[2]
        connection.commit()
    store.assert_activated()


def _inspect_memory(source: Path, target: Path, plan: _Plan, *, current_scope: str) -> None:
    source_memory = source / "data/memory"
    target_memory = target / "data/memory"
    try:
        point_scopes, target_point_scopes = _inspect_qdrant(
            source_memory, target_memory, plan, current_scope=current_scope
        )
    except LegacyImportError as exc:
        if exc.code == "LEGACY_DATA_TARGET_MEMORY_INVALID":
            raise
        point_scopes = {}
        target_point_scopes = {}
        plan.recoverable_errors += 1
        plan.digest_items.append("memory\0qdrant-unreadable")
    except Exception:  # noqa: BLE001 - other Memory subdomains remain inspectable
        point_scopes = {}
        target_point_scopes = {}
        plan.recoverable_errors += 1
        plan.digest_items.append("memory\0qdrant-unreadable")
    try:
        _inspect_history_database(
            source_memory,
            target_memory,
            plan,
            point_scopes,
            target_point_scopes,
            current_scope,
        )
    except LegacyImportError as exc:
        if exc.code == "LEGACY_DATA_TARGET_MEMORY_INVALID":
            raise
        plan.recoverable_errors += 1
        plan.digest_items.append("memory\0history-unreadable")
    except Exception:  # noqa: BLE001 - preserve and report at apply time
        plan.recoverable_errors += 1
        plan.digest_items.append("memory\0history-unreadable")
    try:
        _inspect_profiles(source_memory, target_memory, plan)
    except LegacyImportError as exc:
        if exc.code == "LEGACY_DATA_TARGET_MEMORY_INVALID":
            raise
        plan.recoverable_errors += 1
        plan.digest_items.append("memory\0profiles-unreadable")
    except Exception:  # noqa: BLE001 - preserve and report at apply time
        plan.recoverable_errors += 1
        plan.digest_items.append("memory\0profiles-unreadable")


def _merge_memory(
    source: Path,
    payload: Path,
    *,
    overwrite_conflicts: bool,
    current_scope: str,
    quarantine: Path,
) -> None:
    source_memory = source / "data/memory"
    target_memory = payload / "data/memory"
    _validate_target_memory(target_memory)
    target_memory.mkdir(parents=True, exist_ok=True)
    try:
        point_scopes, target_point_scopes = _merge_qdrant(
            source_memory,
            target_memory,
            overwrite_conflicts=overwrite_conflicts,
            current_scope=current_scope,
            quarantine=quarantine,
        )
    except LegacyImportError as exc:
        if exc.code != "LEGACY_DATA_SOURCE_MEMORY_INVALID":
            raise
        point_scopes = {}
        target_point_scopes = {}
        _quarantine_memory_path(source_memory / "qdrant", quarantine / "qdrant")
    try:
        _merge_history_database(
            source_memory,
            target_memory,
            point_scopes,
            target_point_scopes,
            current_scope,
            overwrite_conflicts=overwrite_conflicts,
            quarantine=quarantine,
        )
    except LegacyImportError as exc:
        if exc.code != "LEGACY_DATA_SOURCE_MEMORY_INVALID":
            raise
        _quarantine_sqlite(source_memory, quarantine)
    try:
        _merge_profiles(
            source_memory, target_memory, overwrite_conflicts=overwrite_conflicts
        )
    except LegacyImportError as exc:
        if exc.code != "LEGACY_DATA_SOURCE_MEMORY_INVALID":
            raise
        _quarantine_memory_path(
            source_memory / "core_profiles.json", quarantine / "core_profiles.json"
        )
    # Imported Timeline invalidates every old cursor. Core will rebuild role by
    # role from the merged authoritative stores.
    shutil.rmtree(target_memory / "curation_state", ignore_errors=True)


def _validate_target_memory(memory: Path) -> None:
    """Fail closed before any source record can mutate a copied target store."""

    client = None
    try:
        _points, client = _qdrant_points(memory)
    except Exception as exc:
        raise LegacyImportError(
            "LEGACY_DATA_TARGET_MEMORY_INVALID", "staging"
        ) from exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            (memory / "qdrant/.lock").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="sakura-target-memory-validation-") as temporary:
        try:
            _history_rows(memory, Path(temporary) / "history.sqlite3")
            _profiles(memory)
        except Exception as exc:
            raise LegacyImportError(
                "LEGACY_DATA_TARGET_MEMORY_INVALID", "staging"
            ) from exc


def _quarantine_memory_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        copy_tree_checked(source, destination, cancelled=lambda: False, skip_noise=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _quarantine_sqlite(source_memory: Path, quarantine: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        name = f"mem0_history.db{suffix}"
        _quarantine_memory_path(source_memory / name, quarantine / name)


def _append_memory_quarantine(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
            )
            + "\n"
        )


def _qdrant_client(path: Path):
    from plugins.builtin.sakura_mem0.memory import (
        _install_disabled_qdrant_grpc_module,
        _install_synchronous_qdrant_client_facade,
    )

    _install_disabled_qdrant_grpc_module()
    _install_synchronous_qdrant_client_facade()
    from qdrant_client import QdrantClient

    return QdrantClient(path=path.as_posix())


def _qdrant_points(memory: Path) -> tuple[dict[str, tuple[Any, Any]], Any | None]:
    root = memory / "qdrant"
    if not root.is_dir() or not any(path.is_file() for path in root.rglob("*")):
        return {}, None
    client = _qdrant_client(root)
    try:
        names = {collection.name for collection in client.get_collections().collections}
        if "sakura_memories" not in names:
            return {}, client
        return _scroll_qdrant_points(client), client
    except Exception:
        client.close()
        (root / ".lock").unlink(missing_ok=True)
        raise


def _scroll_qdrant_points(client: Any) -> dict[str, tuple[Any, Any]]:
    points: dict[str, tuple[Any, Any]] = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name="sakura_memories",
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for record in records:
            points[str(record.id)] = (record.vector, dict(record.payload or {}))
        if offset is None:
            break
    return points


def _target_qdrant_scopes(memory: Path) -> dict[str, _ScopeResolution]:
    client = None
    try:
        points, client = _qdrant_points(memory)
        return {
            point_id: _point_scope(payload, "")
            for point_id, (_vector, payload) in points.items()
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            (memory / "qdrant/.lock").unlink(missing_ok=True)


def _resolve_scope(values: Iterable[Any], fallback: str = "") -> _ScopeResolution:
    scopes: list[str] = []
    for value in values:
        scope = str(value or "").strip()
        if scope and scope not in scopes:
            scopes.append(scope)
    if scopes:
        return _ScopeResolution(scopes[0], len(scopes) > 1)
    return _ScopeResolution(str(fallback or "").strip())


def _point_scope(payload: dict[str, Any], current_scope: str) -> _ScopeResolution:
    return _resolve_scope(
        (payload.get("user_id"), payload.get("scope")),
        fallback=current_scope,
    )


def _inspect_qdrant(
    source_memory: Path,
    target_memory: Path,
    plan: _Plan,
    *,
    current_scope: str,
) -> tuple[dict[str, _ScopeResolution], dict[str, _ScopeResolution]]:
    try:
        source_points, source_client = _qdrant_points(source_memory)
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_SOURCE_MEMORY_INVALID", "inspect") from exc
    try:
        target_points, target_client = _qdrant_points(target_memory)
    except Exception as exc:
        if source_client is not None:
            source_client.close()
            (source_memory / "qdrant/.lock").unlink(missing_ok=True)
        raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "inspect") from exc
    scopes: dict[str, _ScopeResolution] = {}
    target_scopes = {
        point_id: _point_scope(payload, "")
        for point_id, (_vector, payload) in target_points.items()
    }
    try:
        for point_id, (vector, payload) in source_points.items():
            resolution = _point_scope(payload, current_scope)
            scope = resolution.scope
            if resolution.conflict:
                scopes[point_id] = resolution
                plan.classify(
                    domain="memory",
                    character_id=scope or "scope-conflict",
                    item_id=point_id,
                    status="conflicts",
                    signature=_signature((vector, payload)),
                    hard=True,
                )
                continue
            if not scope:
                plan.recoverable_errors += 1
                plan.digest_items.append(
                    f"memory\0unscoped-point\0{point_id}\0{_signature((vector, payload))}"
                )
                continue
            scopes[point_id] = resolution
            payload = {**payload, "user_id": scope}
            existing = target_points.get(point_id)
            source_signature = _signature((vector, payload))
            target_resolution = target_scopes.get(point_id, _ScopeResolution(""))
            hard = bool(
                existing
                and (
                    target_resolution.conflict
                    or not target_resolution.scope
                    or target_resolution.scope != scope
                )
            )
            status = "new" if existing is None else (
                "identical" if _signature(existing) == source_signature else "conflicts"
            )
            if hard:
                status = "conflicts"
            plan.classify(
                domain="memory",
                character_id=scope,
                item_id=point_id,
                status=status,
                signature=_signature(
                    {"source": source_signature, "target": existing}
                ),
                hard=hard,
            )
    finally:
        for client, root in (
            (source_client, source_memory / "qdrant"),
            (target_client, target_memory / "qdrant"),
        ):
            if client is not None:
                client.close()
                (root / ".lock").unlink(missing_ok=True)
    return scopes, target_scopes


def _merge_qdrant(
    source_memory: Path,
    target_memory: Path,
    *,
    overwrite_conflicts: bool,
    current_scope: str,
    quarantine: Path,
) -> tuple[dict[str, _ScopeResolution], dict[str, _ScopeResolution]]:
    source_client = None
    source_has_collection = False
    try:
        source_points, source_client = _qdrant_points(source_memory)
        if source_client is not None:
            source_names = {
                item.name for item in source_client.get_collections().collections
            }
            if "sakura_memories" in source_names:
                source_has_collection = True
                source_info = source_client.get_collection("sakura_memories")
                vectors_config = source_info.config.params.vectors
                sparse_vectors_config = source_info.config.params.sparse_vectors
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_SOURCE_MEMORY_INVALID", "staging") from exc
    finally:
        if source_client is not None:
            try:
                source_client.close()
            except Exception:
                pass
            (source_memory / "qdrant/.lock").unlink(missing_ok=True)

    if not source_has_collection:
        try:
            return {}, _target_qdrant_scopes(target_memory)
        except Exception as exc:
            raise LegacyImportError(
                "LEGACY_DATA_TARGET_MEMORY_INVALID", "staging"
            ) from exc

    scopes: dict[str, _ScopeResolution] = {}
    scoped_points: list[tuple[str, Any, dict[str, Any]]] = []
    for point_id, (vector, raw_payload) in source_points.items():
        payload = dict(raw_payload)
        resolution = _point_scope(payload, current_scope)
        if resolution.conflict:
            raise LegacyImportError("LEGACY_DATA_SCOPE_CONFLICT", "staging")
        if not resolution.scope:
            _append_memory_quarantine(
                quarantine / "unscoped-qdrant-points.jsonl",
                {
                    "code": "LEGACY_MEMORY_SCOPE_UNRESOLVED",
                    "id": point_id,
                    "vector": vector,
                    "payload": raw_payload,
                },
            )
            continue
        payload["user_id"] = resolution.scope
        scopes[point_id] = resolution
        scoped_points.append((point_id, vector, payload))

    if not scoped_points:
        try:
            return scopes, _target_qdrant_scopes(target_memory)
        except Exception as exc:
            raise LegacyImportError(
                "LEGACY_DATA_TARGET_MEMORY_INVALID", "staging"
            ) from exc

    target_root = target_memory / "qdrant"
    target_root.mkdir(parents=True, exist_ok=True)
    try:
        target_client = _qdrant_client(target_root)
        target_names = {item.name for item in target_client.get_collections().collections}
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "staging") from exc
    target_scopes: dict[str, _ScopeResolution] = {}
    try:
        if "sakura_memories" not in target_names:
            try:
                target_client.create_collection(
                    collection_name="sakura_memories",
                    vectors_config=vectors_config,
                    sparse_vectors_config=sparse_vectors_config,
                )
            except Exception as exc:
                raise LegacyImportError(
                    "LEGACY_DATA_TARGET_MEMORY_WRITE_FAILED", "staging"
                ) from exc
        try:
            target_points = _scroll_qdrant_points(target_client)
        except Exception as exc:
            raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "staging") from exc
        target_scopes = {
            point_id: _point_scope(payload, "")
            for point_id, (_vector, payload) in target_points.items()
        }
        from qdrant_client.models import PointStruct

        for point_id, vector, payload in scoped_points:
            scope = scopes[point_id].scope
            existing = target_points.get(point_id)
            if existing is not None:
                existing_scope = target_scopes[point_id]
                if (
                    existing_scope.conflict
                    or not existing_scope.scope
                    or existing_scope.scope != scope
                ):
                    raise LegacyImportError("LEGACY_DATA_SCOPE_CONFLICT", "staging")
                if _signature(existing) == _signature((vector, payload)):
                    continue
                if not overwrite_conflicts:
                    raise LegacyImportError(
                        "LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED", "staging"
                    )
            try:
                target_client.upsert(
                    collection_name="sakura_memories",
                    points=[
                        PointStruct(
                            id=int(point_id) if point_id.isdecimal() else point_id,
                            vector=vector,
                            payload=payload,
                        )
                    ],
                    wait=True,
                )
            except Exception as exc:
                raise LegacyImportError(
                    "LEGACY_DATA_TARGET_MEMORY_WRITE_FAILED", "staging"
                ) from exc
    finally:
        try:
            target_client.close()
        except Exception:
            pass
        (target_memory / "qdrant/.lock").unlink(missing_ok=True)
    return scopes, target_scopes


def _sqlite_snapshot(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
    from plugins.builtin.sakura_mem0.memory import normalize_existing_history_database

    normalize_existing_history_database(destination)
    return True


def _history_rows(memory: Path, scratch: Path) -> tuple[list[str], dict[str, tuple[Any, ...]]]:
    if not _sqlite_snapshot(memory / "mem0_history.db", scratch):
        return [], {}
    with closing(sqlite3.connect(scratch)) as connection:
        columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(history)")]
        if "id" not in columns:
            raise LegacyImportError("LEGACY_MEMORY_SCHEMA_INVALID", "inspect")
        rows = connection.execute(
            f"SELECT {', '.join(_quote_identifier(name) for name in columns)} "
            "FROM history ORDER BY id"
        ).fetchall()
    id_index = columns.index("id")
    return columns, {str(row[id_index]): tuple(row) for row in rows}


def _canonical_history_row(
    columns: list[str],
    row: tuple[Any, ...],
    point_scopes: dict[str, _ScopeResolution],
    current_scope: str,
) -> tuple[tuple[str, ...], dict[str, Any], _ScopeResolution]:
    source_map = dict(zip(columns, row, strict=True))
    memory_id = str(source_map.get("memory_id") or "")
    point_resolution = point_scopes.get(memory_id, _ScopeResolution(""))
    resolution = _resolve_scope(
        (source_map.get("user_id"), point_resolution.scope),
        fallback=current_scope,
    )
    if point_resolution.conflict:
        resolution = _ScopeResolution(
            resolution.scope or point_resolution.scope,
            True,
        )
    canonical_columns = tuple(dict.fromkeys([*columns, "user_id"]))
    canonical = {name: source_map.get(name) for name in canonical_columns}
    if resolution.scope:
        canonical["user_id"] = resolution.scope
    return canonical_columns, canonical, resolution


def _inspect_history_database(
    source_memory: Path,
    target_memory: Path,
    plan: _Plan,
    point_scopes: dict[str, _ScopeResolution],
    target_point_scopes: dict[str, _ScopeResolution],
    current_scope: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="sakura-memory-history-") as temporary:
        root = Path(temporary)
        try:
            source_columns, source_rows = _history_rows(
                source_memory, root / "source.sqlite3"
            )
        except Exception as exc:
            raise LegacyImportError(
                "LEGACY_DATA_SOURCE_MEMORY_INVALID", "inspect"
            ) from exc
        try:
            target_columns, target_rows = _history_rows(
                target_memory, root / "target.sqlite3"
            )
        except Exception as exc:
            raise LegacyImportError(
                "LEGACY_DATA_TARGET_MEMORY_INVALID", "inspect"
            ) from exc
    final_point_scopes = {**target_point_scopes, **point_scopes}
    for row_id, row in source_rows.items():
        canonical_columns, source_map, source_resolution = _canonical_history_row(
            source_columns,
            row,
            final_point_scopes,
            current_scope,
        )
        scope = source_resolution.scope
        if not scope:
            plan.recoverable_errors += 1
            plan.digest_items.append(
                f"memory\0unscoped-history\0{row_id}\0{_signature(source_map)}"
            )
            continue
        existing = target_rows.get(row_id)
        target_map: dict[str, Any] | None = None
        target_resolution = _ScopeResolution("")
        if existing is not None:
            _columns, target_full, target_resolution = _canonical_history_row(
                target_columns,
                existing,
                target_point_scopes,
                "",
            )
            target_map = {name: target_full.get(name) for name in canonical_columns}
        hard = source_resolution.conflict or bool(
            existing is not None
            and (
                target_resolution.conflict
                or not target_resolution.scope
                or target_resolution.scope != scope
            )
        )
        status = "new" if existing is None else (
            "identical" if _signature(source_map) == _signature(target_map) else "conflicts"
        )
        if hard:
            status = "conflicts"
        plan.classify(
            domain="memory",
            character_id=scope,
            item_id=f"history:{row_id}",
            status=status,
            signature=_signature({"source": source_map, "target": target_map}),
            hard=hard,
        )


def _merge_history_database(
    source_memory: Path,
    target_memory: Path,
    point_scopes: dict[str, _ScopeResolution],
    target_point_scopes: dict[str, _ScopeResolution],
    current_scope: str,
    *,
    overwrite_conflicts: bool,
    quarantine: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="sakura-memory-history-") as temporary:
        source_copy = Path(temporary) / "source.sqlite3"
        try:
            source_columns, source_rows = _history_rows(source_memory, source_copy)
        except Exception as exc:
            raise LegacyImportError("LEGACY_DATA_SOURCE_MEMORY_INVALID", "staging") from exc
        if not source_rows:
            # An empty but valid history database still carries a compatible
            # schema and proves that the legacy Memory domain existed. Keep
            # the normalized snapshot when the target has no database instead
            # of silently turning a successful migration into an empty folder.
            target_path = target_memory / "mem0_history.db"
            if source_copy.is_file() and not target_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_copy, target_path)
            return
        prepared: list[tuple[str, tuple[str, ...], dict[str, Any], str]] = []
        final_point_scopes = {**target_point_scopes, **point_scopes}
        for row_id, row in source_rows.items():
            canonical_columns, source_map, resolution = _canonical_history_row(
                source_columns,
                row,
                final_point_scopes,
                current_scope,
            )
            if resolution.conflict:
                raise LegacyImportError("LEGACY_DATA_SCOPE_CONFLICT", "staging")
            if not resolution.scope:
                _append_memory_quarantine(
                    quarantine / "unscoped-history-rows.jsonl",
                    {
                        "code": "LEGACY_MEMORY_SCOPE_UNRESOLVED",
                        "id": row_id,
                        "columns": list(source_columns),
                        "values": list(row),
                    },
                )
                continue
            prepared.append((row_id, canonical_columns, source_map, resolution.scope))
        if not prepared:
            return
        target_path = target_memory / "mem0_history.db"
        from plugins.builtin.sakura_mem0.memory import normalize_existing_history_database

        try:
            normalize_existing_history_database(target_path)
            connection = sqlite3.connect(target_path)
            target_columns = [
                str(row[1]) for row in connection.execute("PRAGMA table_info(history)")
            ]
        except Exception as exc:
            raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "staging") from exc
        with closing(connection):
            try:
                for name in prepared[0][1]:
                    if name not in target_columns:
                        connection.execute(
                            f"ALTER TABLE history ADD COLUMN {_quote_identifier(name)} TEXT"
                        )
                        target_columns.append(name)
                for row_id, canonical_columns, source_map, scope in prepared:
                    existing = connection.execute(
                        f"SELECT {', '.join(_quote_identifier(name) for name in target_columns)} "
                        "FROM history WHERE id = ?",
                        (row_id,),
                    ).fetchone()
                    values = [source_map.get(name) for name in canonical_columns]
                    if existing is not None:
                        _columns, target_map, target_resolution = _canonical_history_row(
                            target_columns,
                            tuple(existing),
                            target_point_scopes,
                            "",
                        )
                        if (
                            target_resolution.conflict
                            or not target_resolution.scope
                            or target_resolution.scope != scope
                        ):
                            raise LegacyImportError("LEGACY_DATA_SCOPE_CONFLICT", "staging")
                        target_values = [target_map.get(name) for name in canonical_columns]
                        if _signature(target_values) == _signature(values):
                            continue
                        if not overwrite_conflicts:
                            raise LegacyImportError(
                                "LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED", "staging"
                            )
                    placeholders = ", ".join("?" for _ in canonical_columns)
                    assignments = ", ".join(
                        f"{_quote_identifier(name)}=excluded.{_quote_identifier(name)}"
                        for name in canonical_columns
                        if name != "id"
                    )
                    connection.execute(
                        f"INSERT INTO history ({', '.join(_quote_identifier(name) for name in canonical_columns)}) "
                        f"VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {assignments}",
                        values,
                    )
                connection.commit()
            except LegacyImportError:
                connection.rollback()
                raise
            except Exception as exc:
                connection.rollback()
                raise LegacyImportError(
                    "LEGACY_DATA_TARGET_MEMORY_WRITE_FAILED", "staging"
                ) from exc


def _profiles(memory: Path) -> dict[str, Any]:
    path = memory / "core_profiles.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyImportError("LEGACY_MEMORY_PROFILE_INVALID", "inspect") from exc
    if not isinstance(value, dict):
        raise LegacyImportError("LEGACY_MEMORY_PROFILE_INVALID", "inspect")
    return value


def _inspect_profiles(source: Path, target: Path, plan: _Plan) -> None:
    try:
        source_profiles = _profiles(source)
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_SOURCE_MEMORY_INVALID", "inspect") from exc
    try:
        target_profiles = _profiles(target)
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "inspect") from exc
    for scope, profile in source_profiles.items():
        if not isinstance(scope, str) or not scope.strip() or not isinstance(profile, dict):
            plan.recoverable_errors += 1
            continue
        existing = target_profiles.get(scope)
        status = "new" if existing is None else (
            "identical" if _signature(existing) == _signature(profile) else "conflicts"
        )
        plan.classify(
            domain="memory",
            character_id=scope,
            item_id=f"profile:{scope}",
            status=status,
            signature=_signature({"source": profile, "target": existing}),
        )


def _merge_profiles(source: Path, target: Path, *, overwrite_conflicts: bool) -> None:
    try:
        source_profiles = _profiles(source)
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_SOURCE_MEMORY_INVALID", "staging") from exc
    try:
        target_profiles = _profiles(target)
    except Exception as exc:
        raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "staging") from exc
    changed = False
    for scope, profile in source_profiles.items():
        if not isinstance(scope, str) or not scope.strip() or not isinstance(profile, dict):
            continue
        existing = target_profiles.get(scope)
        if existing is not None and _signature(existing) != _signature(profile):
            if not overwrite_conflicts:
                raise LegacyImportError("LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED", "staging")
        if _signature(existing) != _signature(profile):
            target_profiles[scope] = profile
            changed = True
    if changed:
        path = target / "core_profiles.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(target_profiles, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise LegacyImportError(
                "LEGACY_DATA_TARGET_MEMORY_WRITE_FAILED", "staging"
            ) from exc


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_default(item: Any) -> Any:
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(item, bytes):
        return {"bytes": item.hex()}
    return str(item)


def _signature(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["inspect_character_data_import", "run_character_data_import"]
