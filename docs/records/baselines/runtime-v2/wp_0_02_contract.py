"""WP-0-02 sanitized data compatibility acceptance oracle.

This module is test-only. It never reads the repository's real ``data/`` tree and
never imports Runtime v2 or Tauri production code. All mutation and fault
injection happens in a copy of the committed synthetic fixture under ``temp/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


SUPPORTED_CONFIG_VERSION = 4
PRIVATE_CONFIG_SCHEMA_VERSION = 1
FIXTURE_MANIFEST = "FIXTURE-MANIFEST.json"
EXPECTED_CATEGORIES = {
    "characters",
    "api_core_config",
    "system_mcp_plugin_config",
    "chat_history",
    "memory_and_curation",
    "reminders_tasks_notes",
    "runtime_events_visual_observations",
    "plugin_data_user_resources",
    "migration_backup_compatibility",
    "runtime_v2_private_config",
}


class ContractError(RuntimeError):
    """Base error for the fixed WP-0-02 acceptance contract."""


class UnsafeWriteBlocked(ContractError):
    """A write was correctly refused because the dataset is not writable."""


class InjectedFailure(ContractError):
    """A deterministic acceptance-only storage failure."""


@dataclass(frozen=True)
class CompatibilityState:
    mode: str
    reason: str
    config_version: int | None

    @property
    def writable(self) -> bool:
        return self.mode == "read_write"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_manifest(root: Path) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "length": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def validate_fixture_root(fixture_root: Path) -> dict[str, Any]:
    fixture_root = Path(fixture_root).resolve()
    manifest_path = fixture_root / FIXTURE_MANIFEST
    if not manifest_path.is_file():
        raise ContractError(f"missing fixture marker: {manifest_path}")
    manifest = _load_json_object(manifest_path)
    if manifest.get("contract_version") != 1 or manifest.get("synthetic") is not True:
        raise ContractError("fixture marker must declare contract_version=1 and synthetic=true")
    if manifest.get("supported_config_version") != SUPPORTED_CONFIG_VERSION:
        raise ContractError("fixture supported_config_version is out of sync")

    dataset_name = manifest.get("dataset_root")
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        raise ContractError("fixture dataset_root is missing")
    dataset_root = (fixture_root / dataset_name).resolve()
    _require_descendant(dataset_root, fixture_root, "fixture dataset")
    if not dataset_root.is_dir():
        raise ContractError(f"fixture dataset does not exist: {dataset_root}")

    categories = manifest.get("categories")
    if not isinstance(categories, list):
        raise ContractError("fixture categories must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for raw in categories:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ContractError("fixture category is invalid")
        category_id = raw["id"]
        by_id[category_id] = raw
        representatives = raw.get("representative_paths", [])
        missing = raw.get("missing_samples", [])
        if not isinstance(representatives, list) or not isinstance(missing, list):
            raise ContractError(f"fixture category lists are invalid: {category_id}")
        if not representatives and not missing:
            raise ContractError(f"fixture category has neither sample nor missing reason: {category_id}")
        for relative in representatives:
            if not isinstance(relative, str):
                raise ContractError(f"fixture path is not text: {category_id}")
            candidate = (dataset_root / relative).resolve()
            _require_descendant(candidate, dataset_root, "fixture representative")
            if not candidate.is_file():
                raise ContractError(f"fixture representative is missing: {relative}")

    if set(by_id) != EXPECTED_CATEGORIES:
        missing = sorted(EXPECTED_CATEGORIES - set(by_id))
        extra = sorted(set(by_id) - EXPECTED_CATEGORIES)
        raise ContractError(f"fixture categories mismatch; missing={missing}, extra={extra}")

    _assert_no_secrets(fixture_root)
    return manifest


def assess_dataset(dataset_root: Path) -> CompatibilityState:
    dataset_root = Path(dataset_root).resolve()
    try:
        config = _load_yaml_mapping(dataset_root / "data/config/system_config.yaml")
        raw_version = config.get("config_version", 0)
        if isinstance(raw_version, bool):
            raise ContractError("config_version must be an integer")
        config_version = int(raw_version)
        if config_version < 0:
            raise ContractError("config_version must not be negative")
        if config_version > SUPPORTED_CONFIG_VERSION:
            return CompatibilityState(
                mode="diagnostics_read_only",
                reason="unsupported_future_config_version",
                config_version=config_version,
            )
        if config_version < SUPPORTED_CONFIG_VERSION:
            return CompatibilityState(
                mode="diagnostics_read_only",
                reason="legacy_migration_required",
                config_version=config_version,
            )
        _validate_current_dataset(dataset_root)
    except (ContractError, OSError, UnicodeError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return CompatibilityState(
            mode="diagnostics_read_only",
            reason=f"corrupt_or_unsupported:{exc}",
            config_version=None,
        )
    return CompatibilityState(
        mode="read_write",
        reason="supported_legacy_dataset",
        config_version=config_version,
    )


def append_compatible_history(dataset_root: Path) -> None:
    state = assess_dataset(dataset_root)
    if not state.writable:
        raise UnsafeWriteBlocked(state.reason)
    path = Path(dataset_root) / "data/chat_history/fixture.jsonl"
    entry = {
        "created_at": "2000-01-01T00:00:02+00:00",
        "role": "assistant",
        "content": "[REDACTED_FIXTURE_TAURI_COMPATIBLE_WRITE]",
        "translation": "[REDACTED_FIXTURE_TRANSLATION]",
        "tone": "neutral",
        "portrait": "neutral",
        "compat_source": "tauri-v2-fixture",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def strong_atomic_write_json(
    target: Path,
    payload: dict[str, Any],
    *,
    fail_at: str | None = None,
) -> dict[str, str]:
    """Acceptance-only reference for mandatory-backup whole-file writes.

    This is deliberately fixed to JSON fixture files. It is not a migration
    framework and must not be imported by production code.
    """

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)
    original_hash = sha256_file(target) if target.is_file() else ""
    backup = target.with_name(target.name + ".compat.bak")
    backup_temp: Path | None = None
    target_temp: Path | None = None

    try:
        if target.is_file():
            if fail_at == "backup":
                raise InjectedFailure("backup creation failed")
            backup_temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak.tmp")
            _write_bytes_fsync(backup_temp, target.read_bytes())
            if sha256_file(backup_temp) != original_hash:
                raise ContractError("backup verification failed")
            os.replace(backup_temp, backup)
            backup_temp = None

        fd, temp_name = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        target_temp = Path(temp_name)
        if fail_at == "temp_write":
            os.close(fd)
            raise InjectedFailure("temporary file write failed")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(target_temp.read_text(encoding="utf-8"))

        if fail_at == "interrupt_after_temp":
            return {
                "status": "interrupted",
                "target_sha256": sha256_file(target) if target.is_file() else "",
                "orphan_temp": target_temp.name,
            }
        if fail_at == "replace":
            raise InjectedFailure("atomic replace failed")
        os.replace(target_temp, target)
        target_temp = None
        return {
            "status": "committed",
            "target_sha256": sha256_file(target),
            "backup_sha256": sha256_file(backup) if backup.is_file() else "",
        }
    except BaseException:
        if target_temp is not None:
            target_temp.unlink(missing_ok=True)
        if backup_temp is not None:
            backup_temp.unlink(missing_ok=True)
        raise


def run_contract(fixture_root: Path, output_root: Path) -> dict[str, Any]:
    fixture_root = Path(fixture_root).resolve()
    output_root = Path(output_root).resolve()
    validate_fixture_root(fixture_root)
    source_before = tree_manifest(fixture_root)
    if output_root.exists():
        raise ContractError(f"output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    source_dataset = fixture_root / "dataset"

    scenarios: dict[str, dict[str, Any]] = {}

    normal = _copy_dataset(source_dataset, output_root / "normal")
    before_count = len(_read_jsonl(normal / "data/chat_history/fixture.jsonl", _validate_chat_record))
    _require_writable(normal)
    append_compatible_history(normal)
    after_count = len(_read_jsonl(normal / "data/chat_history/fixture.jsonl", _validate_chat_record))
    _require_writable(normal)
    if after_count != before_count + 1:
        raise ContractError("compatible history append was not readable by the legacy parser")
    scenarios["normal_qt_tauri_qt"] = {"status": "passed", "history_records": after_count}

    for name, fail_at in (
        ("backup_failure", "backup"),
        ("temporary_write_failure", "temp_write"),
        ("atomic_replace_failure", "replace"),
    ):
        root = _copy_dataset(source_dataset, output_root / name)
        target = root / "data/tasks.json"
        original_hash = sha256_file(target)
        try:
            strong_atomic_write_json(target, _tasks_payload("failure-probe"), fail_at=fail_at)
        except InjectedFailure:
            pass
        else:
            raise ContractError(f"fault injection did not fail: {name}")
        if sha256_file(target) != original_hash:
            raise ContractError(f"original target changed after {name}")
        _require_writable(root)
        scenarios[name] = {"status": "passed", "original_preserved": True}

    interrupted = _copy_dataset(source_dataset, output_root / "interrupted")
    interrupted_target = interrupted / "data/tasks.json"
    interrupted_hash = sha256_file(interrupted_target)
    result = strong_atomic_write_json(
        interrupted_target,
        _tasks_payload("interrupt-probe"),
        fail_at="interrupt_after_temp",
    )
    if result.get("status") != "interrupted" or sha256_file(interrupted_target) != interrupted_hash:
        raise ContractError("interruption did not preserve the original target")
    _require_writable(interrupted)
    scenarios["abnormal_interruption"] = {
        "status": "passed",
        "original_preserved": True,
        "orphan_temp_ignored": True,
    }

    corrupt = _copy_dataset(source_dataset, output_root / "corrupt")
    (corrupt / "data/tasks.json").write_text('{"tasks": [', encoding="utf-8")
    corrupt_state = assess_dataset(corrupt)
    _assert_read_only_and_write_blocked(corrupt, corrupt_state)
    scenarios["corrupt_file"] = {"status": "passed", "mode": corrupt_state.mode}

    future = _copy_dataset(source_dataset, output_root / "future")
    future_system = future / "data/config/system_config.yaml"
    future_mapping = _load_yaml_mapping(future_system)
    future_mapping["config_version"] = SUPPORTED_CONFIG_VERSION + 100
    future_system.write_text(
        yaml.safe_dump(future_mapping, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    future_state = assess_dataset(future)
    _assert_read_only_and_write_blocked(future, future_state)
    scenarios["future_schema"] = {
        "status": "passed",
        "mode": future_state.mode,
        "config_version": future_state.config_version,
    }

    source_after = tree_manifest(fixture_root)
    if source_before != source_after:
        raise ContractError("committed fixture changed during acceptance run")
    if any(result.get("status") != "passed" for result in scenarios.values()):
        raise ContractError("one or more scenarios failed")

    report = {
        "contract": "WP-0-02",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "supported_config_version": SUPPORTED_CONFIG_VERSION,
        "fixture_files": len(source_before),
        "fixture_tree_sha256": _manifest_digest(source_before),
        "scenarios": scenarios,
        "fixture_unchanged": True,
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _validate_current_dataset(root: Path) -> None:
    characters = _load_yaml_mapping(root / "data/config/characters.yaml")
    current_character_id = characters.get("current_character_id")
    if not isinstance(current_character_id, str) or not current_character_id.strip():
        raise ContractError("characters.yaml current_character_id is missing")
    character = _load_json_object(root / f"characters/{current_character_id}/character.json")
    if character.get("id") != current_character_id:
        raise ContractError("character id does not match characters.yaml")
    if not isinstance(character.get("display_name"), str):
        raise ContractError("character display_name is missing")
    package = root / "characters" / current_character_id
    card = _required_relative_file(package, character.get("card"), "character card")
    portrait = character.get("portrait")
    if not isinstance(portrait, dict):
        raise ContractError("character portrait mapping is missing")
    _required_relative_file(package, portrait.get("default"), "default portrait")
    if not card.read_text(encoding="utf-8").strip():
        raise ContractError("character card is empty")

    _load_yaml_mapping(root / "data/config/api.yaml")
    _load_yaml_mapping(root / "data/config/mcp.yaml")
    plugins = yaml.safe_load((root / "data/config/plugins.yaml").read_text(encoding="utf-8"))
    if not isinstance(plugins, list):
        raise ContractError("plugins.yaml must be a list")

    _read_jsonl(root / "data/chat_history/fixture.jsonl", _validate_chat_record)
    _load_json_object(root / "data/memory.json")
    _load_json_object(root / "data/memory/core_profiles.json")
    curation = _load_json_object(root / "data/memory_curation_state.json")
    for key in ("processed_history_count", "pending_turns", "backfill_completed"):
        if key not in curation:
            raise ContractError(f"memory curation field is missing: {key}")
    _load_json_object(root / "data/screen_awareness_state.json")

    reminders = _load_json_object(root / "data/reminders.json")
    tasks = _load_json_object(root / "data/tasks.json")
    if not isinstance(reminders.get("reminders"), list):
        raise ContractError("reminders.json must contain a reminders list")
    if not isinstance(tasks.get("tasks"), list):
        raise ContractError("tasks.json must contain a tasks list")
    (root / "data/notes/fixture-note.txt").read_text(encoding="utf-8")

    _read_jsonl(root / "data/runtime_events/fixture.jsonl", _validate_runtime_event)
    _read_jsonl(root / "data/visual_observations/fixture.jsonl", _validate_visual_observation)
    _load_json_object(root / "data/plugins/fixture_plugin/config.json")
    draft = _load_json_object(root / "data/character_studio/drafts/fixture/draft.json")
    if draft.get("version") != 1:
        raise ContractError("character studio draft version is unsupported")

    _load_yaml_mapping(
        root / "data/migration_backup/20000101-000000_v3_to_v4/data/config/system_config.yaml"
    )
    _read_jsonl(root / "data/chat_history.jsonl.migrated", _validate_chat_record)

    for domain, relative in (
        ("desktop", "data/runtime_v2/config/desktop.json"),
        ("ui", "data/runtime_v2/config/ui.json"),
        ("shell", "data/runtime_v2/state/shell.json"),
    ):
        private = _load_json_object(root / relative)
        if private.get("schema_version") != PRIVATE_CONFIG_SCHEMA_VERSION:
            raise ContractError(f"unsupported private schema: {relative}")
        if private.get("domain") != domain:
            raise ContractError(f"private config domain mismatch: {relative}")


def _assert_read_only_and_write_blocked(root: Path, state: CompatibilityState) -> None:
    if state.mode != "diagnostics_read_only" or state.writable:
        raise ContractError(f"unsafe dataset did not enter read-only mode: {state}")
    history = root / "data/chat_history/fixture.jsonl"
    original_hash = sha256_file(history)
    try:
        append_compatible_history(root)
    except UnsafeWriteBlocked:
        pass
    else:
        raise ContractError("unsafe dataset accepted a compatible write")
    if sha256_file(history) != original_hash:
        raise ContractError("blocked write changed history")


def _require_writable(root: Path) -> CompatibilityState:
    state = assess_dataset(root)
    if not state.writable:
        raise ContractError(f"supported fixture unexpectedly became read-only: {state.reason}")
    return state


def _copy_dataset(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination


def _read_jsonl(path: Path, validator: Callable[[dict[str, Any]], None]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ContractError(f"JSONL record must be an object: {path}:{line_number}")
        validator(value)
        records.append(value)
    if not records:
        raise ContractError(f"JSONL fixture has no records: {path}")
    return records


def _validate_chat_record(value: dict[str, Any]) -> None:
    if not all(isinstance(value.get(key), str) for key in ("created_at", "role", "content")):
        raise ContractError("chat record is missing required string fields")


def _validate_runtime_event(value: dict[str, Any]) -> None:
    if not isinstance(value.get("event_type"), str) or not isinstance(value.get("timestamp"), str):
        raise ContractError("runtime event is missing required fields")
    if not isinstance(value.get("metadata", {}), dict):
        raise ContractError("runtime event metadata must be an object")


def _validate_visual_observation(value: dict[str, Any]) -> None:
    required = ("id", "created_at", "source", "summary")
    if not all(isinstance(value.get(key), str) for key in required):
        raise ContractError("visual observation is missing required fields")
    if any(key in value for key in ("data_url", "image_url")):
        raise ContractError("visual observation fixture must not contain image data")


def _tasks_payload(text: str) -> dict[str, Any]:
    return {
        "tasks": [
            {
                "id": "fixture2",
                "text": f"[REDACTED_FIXTURE_{text.upper()}]",
                "created_at": "2000-01-01T00:00:00+00:00",
                "completed_at": None,
            }
        ]
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"JSON file must contain an object: {path}")
    return value


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"YAML file must contain a mapping: {path}")
    return dict(value)


def _required_relative_file(package: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} path is missing")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ContractError(f"{label} path must be relative")
    resolved = (package / candidate).resolve()
    _require_descendant(resolved, package.resolve(), label)
    if not resolved.is_file():
        raise ContractError(f"{label} file is missing")
    return resolved


def _write_bytes_fsync(path: Path, payload: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _require_descendant(candidate: Path, root: Path, label: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ContractError(f"{label} escapes its root: {candidate}") from exc


def _manifest_digest(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_no_secrets(root: Path) -> None:
    patterns = (
        re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
        re.compile(r"(?im)^\s*api_key\s*:\s*(?!REDACTED_FIXTURE_VALUE\s*$)\S.+$"),
        re.compile(r'(?i)"api_key"\s*:\s*"(?!REDACTED_FIXTURE_VALUE")[^"]+"'),
    )
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ContractError(f"binary file is not allowed in the sanitized fixture: {path}")
        for pattern in patterns:
            if pattern.search(text):
                raise ContractError(f"possible secret found in sanitized fixture: {path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WP-0-02 sanitized compatibility contract")
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    fixture_root = (
        args.fixture_root
        if args.fixture_root is not None
        else repo_root / "tests/fixtures/runtime_v2/wp_0_02"
    ).resolve()
    allowed_fixture = (repo_root / "tests/fixtures/runtime_v2/wp_0_02").resolve()
    if fixture_root != allowed_fixture:
        raise ContractError("CLI runs are restricted to the committed sanitized WP-0-02 fixture")

    temp_root = (repo_root / "temp/runtime-v2-wp-0-02").resolve()
    output_root = (
        args.output_root
        if args.output_root is not None
        else temp_root / f"contract-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    ).resolve()
    _require_descendant(output_root, temp_root, "acceptance output")
    report = run_contract(fixture_root, output_root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
