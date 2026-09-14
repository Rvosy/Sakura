"""Character packages and explicit role identity mapping for incremental import."""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from app.config.character_loader import CharacterRegistry, _load_profile, load_character_system_prompt
from app.storage.paths import sanitize_directory_component, sanitize_file_stem

from .configuration import add_character_extensions
from .errors import LegacyImportError
from .files import copy_tree_checked, is_link_or_junction


def prepare_packages(source: Path, target: Path, converted: Path, mapping: dict[str, str]):
    """Load only packages selected for import; keep existing packages untouched."""
    targets = CharacterRegistry(target, issue_sink=lambda *args: None).profiles
    entries = {}
    issues = []
    root = source / "characters"
    if is_link_or_junction(root):
        raise LegacyImportError("LEGACY_PATH_ESCAPE", "inspect")
    for manifest in sorted(root.glob("*/character.json")):
        try:
            if is_link_or_junction(manifest.parent) or is_link_or_junction(manifest):
                raise ValueError("linked package")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            role = raw.get("id") if isinstance(raw, dict) else None
            if not isinstance(role, str) or not role.strip():
                raise ValueError("missing id")
            role = role.strip()
            if role in entries:
                entries[role].update(packageStatus="unavailable", canImport=False, requiresMapping=False)
                entries[role].pop("staged", None)
                issues.append({"name": manifest.parent.name, "code": "LEGACY_CHARACTER_ID_CONFLICT"})
                continue
        except (OSError, ValueError):
            issues.append({"name": manifest.parent.name, "code": "LEGACY_CHARACTER_MANIFEST_INVALID"})
            continue
        name = raw.get("display_name")
        name = name if isinstance(name, str) and name else role
        selected = mapping.get(role, role)
        same_name = [item.id for item in targets.values() if item.display_name == name and item.id != role]
        entry = {"sourceCharacterId": role, "displayName": name, "targetCharacterId": selected,
                 "packageStatus": "existing" if selected in targets else "new",
                 "requiresMapping": role not in mapping and role not in targets and bool(same_name),
                 "canImport": False}
        entries[role] = entry
        # Existing target identity is an explicit reuse, never a package overwrite.
        if selected in targets:
            entry["canImport"] = role in targets
            continue
        if selected != role:
            # Another package in this same import can own the selected identity.
            # Resolve it after every package has been prepared.
            entry.update(packageStatus="existing")
            continue
        destination_name = sanitize_directory_component(role)
        taken = {sanitize_file_stem(item.id).casefold() for item in targets.values()}
        destination = target / "characters" / destination_name
        if sanitize_file_stem(role).casefold() in taken or destination.exists():
            entry.update(packageStatus="unavailable", requiresMapping=False,
                         errorCode="LEGACY_CHARACTER_ID_CONFLICT")
            continue
        staged_root = converted / "package-previews" / str(len(entries))
        staged = staged_root / "characters" / destination_name
        try:
            copy_tree_checked(manifest.parent, staged, cancelled=lambda: False)
            add_character_extensions(staged_root)
            profile = _load_profile(staged / "character.json")
            load_character_system_prompt(profile)
            if profile.id != role:
                raise ValueError("identity changed")
            entry["canImport"] = True
            entry["staged"] = staged
        except (OSError, ValueError, RuntimeError):
            entry.update(packageStatus="unavailable", requiresMapping=False,
                         errorCode="LEGACY_CHARACTER_LOAD_FAILED")
    for role, entry in entries.items():
        selected = entry["targetCharacterId"]
        if selected != role and selected not in targets and not entries.get(selected, {}).get("staged"):
            raise LegacyImportError("LEGACY_DATA_MAPPING_INVALID", "inspect")
    return entries, targets, issues


def public_packages(entries, targets, scopes, mapping):
    source_ids = set(entries) | set(scopes)
    if set(mapping) - source_ids or any(
        not isinstance(k, str) or not isinstance(v, str) or not k or not v
        for k, v in mapping.items()
    ):
        raise LegacyImportError("LEGACY_DATA_MAPPING_INVALID", "inspect")
    records = []
    available = set(targets) | {role for role, entry in entries.items() if entry.get("staged")}
    for role in sorted(source_ids, key=str.casefold):
        selected = mapping.get(role, role)
        if selected != role and selected not in available:
            raise LegacyImportError("LEGACY_DATA_MAPPING_INVALID", "inspect")
        entry = entries.get(role, {
            "sourceCharacterId": role, "displayName": role,
            "targetCharacterId": selected, "packageStatus": "existing" if selected in available else "missing",
            "requiresMapping": False, "canImport": False,
        })
        records.append({key: value for key, value in entry.items() if key not in {"staged", "manifest"}})
    return records


def install_packages(entries, target: Path, payload: Path) -> None:
    selected = [entry for entry in entries.values() if entry["packageStatus"] == "new" and entry.get("staged")]
    if not selected:
        return
    if (target / "characters").is_dir():
        copy_tree_checked(target / "characters", payload / "characters", cancelled=lambda: False)
    for entry in selected:
        staged = entry["staged"]
        destination = payload / "characters" / staged.name
        if destination.exists():
            raise LegacyImportError("LEGACY_CHARACTER_ID_CONFLICT", "staging")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(destination))


def remap_frozen_data(converted: Path, mapping: dict[str, str], current_scope: str, *, existing: bool = False) -> tuple[str, int, int]:
    """Remap the frozen source; unreadable domains are preserved outside active data."""
    from . import incremental as inc

    changes = {key: value for key, value in mapping.items() if key != value}
    if not changes:
        return current_scope, 0, 0
    memory = converted / "data/memory"
    quarantine = converted / "data/legacy-imports/incremental-scan/quarantine/memory"
    errors = 0
    reassociated = 0

    def preserve(path: Path) -> None:
        nonlocal errors
        if existing:
            raise LegacyImportError("LEGACY_DATA_TARGET_MEMORY_INVALID", "staging")
        if path.exists():
            quarantine.mkdir(parents=True, exist_ok=True)
            # Both paths belong to this operation's private converted tree.
            path.resolve().relative_to(converted.resolve())
            destination = quarantine / path.name
            destination.resolve().relative_to(converted.resolve())
            path.replace(destination)
            errors += 1

    scopes = {}
    client = None
    try:
        points, client = inc._qdrant_points(memory)
        scopes = {key: inc._point_scope(payload, current_scope) for key, (_, payload) in points.items()}
        if client is not None:
            for point_id, (vector, payload) in points.items():
                resolution = scopes[point_id]
                if resolution.conflict:
                    if existing:
                        continue
                    inc._append_memory_quarantine(quarantine / "scope-conflict-points.jsonl", {"id": point_id, "vector": vector, "payload": payload})
                    client.delete(collection_name="sakura_memories", points_selector=[point_id], wait=True)
                    errors += 1
                elif resolution.scope in changes:
                    updated = {**payload, "user_id": changes[resolution.scope]}
                    if "scope" in updated:
                        updated["scope"] = changes[resolution.scope]
                    metadata = updated.get("metadata")
                    if isinstance(metadata, dict) and "scope" in metadata:
                        updated["metadata"] = {**metadata, "scope": changes[resolution.scope]}
                    client.overwrite_payload(collection_name="sakura_memories", payload=updated, points=[point_id], wait=True)
                    reassociated += 1
    except Exception:
        if client is not None:
            client.close()
            client = None
        preserve(memory / "qdrant")
    finally:
        if client is not None:
            client.close()

    database = memory / "mem0_history.db"
    if database.is_file():
        try:
            with closing(sqlite3.connect(database)) as connection:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(history)")]
                rows = connection.execute('SELECT * FROM history').fetchall()
                if "id" not in columns:
                    raise ValueError("history has no id")
                if "user_id" not in columns:
                    connection.execute('ALTER TABLE history ADD COLUMN user_id TEXT')
                for row in rows:
                    _, canonical, resolution = inc._canonical_history_row(columns, row, scopes, current_scope)
                    if resolution.conflict:
                        if existing:
                            continue
                        inc._append_memory_quarantine(quarantine / "scope-conflict-history.jsonl", canonical)
                        connection.execute('DELETE FROM history WHERE id=?', (canonical["id"],))
                        errors += 1
                    elif resolution.scope in changes:
                        connection.execute('UPDATE history SET user_id=? WHERE id=?', (changes[resolution.scope], canonical["id"]))
                        reassociated += 1
                connection.commit()
        except (sqlite3.Error, ValueError, KeyError):
            preserve(database)

    profiles_path = memory / "core_profiles.json"
    if profiles_path.is_file():
        try:
            profiles = inc._profiles(memory)
            result = {}
            for role, profile in profiles.items():
                destination = changes.get(role, role)
                if existing and destination != role and destination in profiles:
                    result[role] = profile
                    continue
                if destination in result:
                    if existing:
                        raise LegacyImportError("LEGACY_DATA_MAPPING_COLLISION", "staging")
                    inc._append_memory_quarantine(quarantine / "profile-conflicts.jsonl", {"scope": role, "profile": profile})
                    errors += 1
                    continue
                if destination != role:
                    reassociated += 1
                if isinstance(profile, dict) and destination != role:
                    profile = dict(profile)
                    metadata = profile.get("metadata")
                    if isinstance(metadata, dict):
                        profile["metadata"] = {**metadata, "scope": destination}
                result[destination] = profile
            profiles_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError, RuntimeError):
            preserve(profiles_path)
    timeline = converted / "data/chat_history/timeline.sqlite3"
    if timeline.is_file():
        with closing(sqlite3.connect(timeline)) as connection:
            cases = ' '.join('WHEN ? THEN ?' for _ in changes)
            values = [item for pair in changes.items() for item in pair]
            placeholders = ','.join('?' for _ in changes)
            reassociated += connection.execute(f'SELECT COUNT(*) FROM timeline_entries WHERE character_id IN ({placeholders})', list(changes)).fetchone()[0]
            connection.execute(f'UPDATE timeline_entries SET character_id=CASE character_id {cases} ELSE character_id END', values)
            connection.commit()
    return changes.get(current_scope, current_scope), errors, reassociated
