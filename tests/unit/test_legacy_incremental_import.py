from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.legacy_import import incremental as legacy_incremental
from app.legacy_import import LegacyImportError
from app.legacy_import.incremental import (
    inspect_character_data_import,
    run_character_data_import,
)
from app.legacy_import.transaction import commit_payload, finalize_commit
from app.storage.timeline import TimelineStore


@pytest.fixture(autouse=True)
def _isolated_preview_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    directory = tmp_path / "system-temp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    monkeypatch.setenv("TMP", str(directory))
    monkeypatch.setenv("TEMP", str(directory))


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "legacy"
    (source / "data/chat_history").mkdir(parents=True)
    (source / "data/memory").mkdir(parents=True)
    (source / "data/config").mkdir(parents=True)
    (source / "data/config/system_config.yaml").write_text("{}\n", encoding="utf-8")
    return source


def _record(timestamp: str, role: str, content: str) -> dict[str, str]:
    return {"created_at": timestamp, "role": role, "content": content}


def _write_history(path: Path, records: list[dict[str, str]], *, tail: bytes = b"") -> None:
    payload = b"".join(
        (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        for record in records
    )
    path.write_bytes(payload + tail)


@pytest.mark.parametrize("linked", [False, True])
def test_preview_refuses_precreated_token_directory(tmp_path: Path, linked: bool) -> None:
    plan = legacy_incremental._Plan("fixture", comparison_items=["private-preview-content"])
    path = legacy_incremental._plan_path(plan.token)
    destination = tmp_path / "precreated"
    destination.mkdir()
    if linked:
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(path.parent), str(destination)],
                capture_output=True, check=True,
            )
        else:
            path.parent.symlink_to(destination, target_is_directory=True)
    else:
        path.parent.mkdir()
    with pytest.raises(FileExistsError):
        legacy_incremental._save_plan(plan, tmp_path, tmp_path, tmp_path)
    assert not path.exists()
    assert list(destination.iterdir()) == []


def test_preview_cleanup_does_not_follow_links_or_recurse(tmp_path: Path) -> None:
    path = legacy_incremental._plan_path("a" * 32)
    path.parent.mkdir(mode=0o700)
    path.write_text("preview", encoding="utf-8")
    extra = path.parent / "unrelated" / "keep.txt"
    extra.parent.mkdir()
    extra.write_text("keep", encoding="utf-8")
    legacy_incremental._remove_plan(path)
    assert extra.read_text(encoding="utf-8") == "keep"
    assert not path.exists()

    link = legacy_incremental._plan_path("b" * 32)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link.parent), str(path.parent)],
            capture_output=True, check=True,
        )
    else:
        link.parent.symlink_to(path.parent, target_is_directory=True)
    path.write_text("private-preview", encoding="utf-8")
    legacy_incremental._remove_plan(link)
    assert path.read_text(encoding="utf-8") == "private-preview"
    assert link.parent.exists()


def test_preview_cleanup_rejects_other_uid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = legacy_incremental._plan_path("c" * 32)
    path.parent.mkdir(mode=0o700)
    path.write_text("other-user-preview", encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: path.parent.lstat().st_uid + 1, raising=False)
    legacy_incremental._remove_plan(path)
    assert path.read_text(encoding="utf-8") == "other-user-preview"


def test_preview_pruning_removes_only_old_owned_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(legacy_incremental, "_MAX_PENDING_PLANS", 1)
    first = legacy_incremental._Plan("first")
    second = legacy_incremental._Plan("second")
    legacy_incremental._save_plan(first, tmp_path, tmp_path, tmp_path)
    first_path = legacy_incremental._plan_path(first.token)
    os.utime(first_path.parent, (1, 1))
    legacy_incremental._save_plan(second, tmp_path, tmp_path, tmp_path)
    assert not first_path.parent.exists()
    assert legacy_incremental._plan_path(second.token).is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_preview_permissions_are_private_and_relaxed_directory_is_rejected(tmp_path: Path) -> None:
    import stat

    previous_umask = os.umask(0)
    try:
        plan = legacy_incremental._Plan("fixture", comparison_items=["private-preview-content"])
        legacy_incremental._save_plan(plan, tmp_path, tmp_path, tmp_path)
    finally:
        os.umask(previous_umask)
    path = legacy_incremental._plan_path(plan.token)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.parent.chmod(0o755)
    assert not legacy_incremental._private_plan_directory(path.parent)
    legacy_incremental._remove_plan(path)
    assert path.is_file()


def test_incremental_plan_survives_cli_process_exit(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _write_history(
        source / "data/chat_history/Sakura.jsonl",
        [_record("2026-01-01T00:00:00+08:00", "user", "private source text")],
    )
    command = [sys.executable, "-m", "app.legacy_import"]
    scope = ["--source", str(source), "--target", str(target)]
    inspected = subprocess.run(
        [*command, "inspect-data", *scope], capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=60,
    )
    plan = json.loads(inspected.stdout)["plan"]
    assert "private source text" not in inspected.stdout
    applied = subprocess.run(
        [*command, "apply-data", *scope, "--import-id", "cross-process",
         "--plan-token", plan["planToken"]],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    )
    assert json.loads(applied.stdout)["type"] == "data-import-result"
    assert "private source text" not in applied.stdout
    assert not legacy_incremental._plan_path(plan["planToken"]).parent.exists()
    from app.legacy_import.transaction import PendingCommit
    finalize_commit(PendingCommit("cross-process", target))
    repeated = inspect_character_data_import(source, target)
    assert repeated["totals"]["historyIdentical"] == 1
    assert repeated["totals"]["historyNew"] == 0


def test_pre_registry_ids_are_reused_and_mapping_preserves_conflict_identity(
    tmp_path: Path,
) -> None:
    from app.storage.timeline import NewTimelineEntry, TimelineKind

    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    history = source / "data/chat_history/Sakura.jsonl"
    timestamp = "2026-01-01T00:00:00+08:00"
    records = [_record(timestamp, "user", "first"), _record(timestamp, "user", "second")]
    _write_history(history, records)
    store = TimelineStore(target / "data/chat_history/timeline.sqlite3")
    store.initialize()
    # Old stored identifiers are opaque fixtures; no legacy digest is computed.
    old_ids = ["legacy-human-" + "a" * 32, "legacy-human-" + "b" * 32]
    old_turns = ["legacy-turn-" + "c" * 32, "legacy-turn-" + "d" * 32]
    for index, record in enumerate(records):
        store.append(NewTimelineEntry(
            entry_id=old_ids[index], turn_id=old_turns[index], character_id="Sakura",
            kind=TimelineKind.HUMAN, origin="chat", created_at=timestamp,
            payload={"text": record["content"]},
        ))
    plan = inspect_character_data_import(source, target)
    assert plan["totals"]["historyIdentical"] == 2
    assert plan["totals"]["historyNew"] == 0
    _, pending = run_character_data_import(
        source, target, plan_token=plan["planToken"], overwrite_conflicts=False,
    )
    finalize_commit(pending)
    records[1]["content"] = "edited"
    _write_history(history, records)
    conflict = inspect_character_data_import(source, target)
    assert conflict["totals"]["historyIdentical"] == 1
    assert conflict["totals"]["historyConflicts"] == 1
    _, pending = run_character_data_import(
        source, target, plan_token=conflict["planToken"], overwrite_conflicts=True,
    )
    finalize_commit(pending)
    entries = store.read_all("Sakura")
    assert [entry.entry_id for entry in entries] == old_ids
    assert [entry.turn_id for entry in entries] == old_turns
    assert [entry.payload["text"] for entry in entries] == ["first", "edited"]


def test_incremental_plan_rejects_same_size_source_change_and_missing_preview(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    history = source / "data/chat_history/Sakura.jsonl"
    records = [_record("2026-01-01T00:00:00+08:00", "user", "before")]
    _write_history(history, records)
    plan = inspect_character_data_import(source, target)
    stat = history.stat()
    records[0]["content"] = "after!"
    _write_history(history, records)
    os.utime(history, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_IMPORT_PLAN_STALE"):
        run_character_data_import(
            source, target, plan_token=plan["planToken"], overwrite_conflicts=True,
        )
    legacy_incremental._plan_path(plan["planToken"]).unlink()
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_IMPORT_PLAN_STALE"):
        run_character_data_import(
            source, target, plan_token=plan["planToken"], overwrite_conflicts=True,
        )
    assert not list(target.glob(".legacy-import-*"))


def test_incremental_history_salvages_dirty_rows_skips_identical_and_prompts_on_conflict(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    history = source / "data/chat_history/Sakura.jsonl"
    records = [
        _record("2026-01-01T00:00:00+08:00", "user", "hello"),
        _record("2026-01-01T00:00:01+08:00", "assistant", "reply"),
    ]
    _write_history(history, records, tail=b'{"created_at":')

    plan = inspect_character_data_import(source, target)
    assert plan["totals"] == {
        "historyNew": 2,
        "historyIdentical": 0,
        "historyConflicts": 0,
        "memoryNew": 0,
        "memoryIdentical": 0,
        "memoryConflicts": 0,
        "recoverableErrors": 1,
    }
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-history-01",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    repeated = inspect_character_data_import(source, target)
    assert repeated["totals"]["historyNew"] == 0
    assert repeated["totals"]["historyIdentical"] == 2

    records[0]["content"] = "changed"
    _write_history(history, records, tail=b'{"created_at":')
    conflict = inspect_character_data_import(source, target)
    assert conflict["totals"]["historyConflicts"] == 1
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_IMPORT_CONFIRMATION_REQUIRED"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-history-02",
            plan_token=str(conflict["planToken"]),
            overwrite_conflicts=False,
        )
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-history-03",
        plan_token=str(conflict["planToken"]),
        overwrite_conflicts=True,
    )
    finalize_commit(pending)
    entries = TimelineStore(target / "data/chat_history/timeline.sqlite3").read_all("Sakura")
    assert entries[0].payload == {"text": "changed"}
    quarantine = target / "data/legacy-imports/incremental-history-03/quarantine/history-records.jsonl"
    assert quarantine.is_file()


def test_incremental_history_append_keeps_partial_assistant_identity(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    history = source / "data/chat_history/Sakura.jsonl"
    records = [
        _record("2026-01-01T00:00:00+08:00", "user", "hello"),
        _record("2026-01-01T00:00:01+08:00", "assistant", "part one"),
    ]
    _write_history(history, records)
    initial = inspect_character_data_import(source, target)
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-append-01",
        plan_token=str(initial["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    records.append(
        _record("2026-01-01T00:00:02+08:00", "assistant", "part two")
    )
    _write_history(history, records)
    appended = inspect_character_data_import(source, target)
    assert appended["totals"]["historyNew"] == 0
    assert appended["totals"]["historyIdentical"] == 1
    assert appended["totals"]["historyConflicts"] == 1


def test_incremental_history_keeps_character_scopes_isolated(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    for scope, content in (("Alpha", "alpha"), ("Beta", "beta")):
        _write_history(
            source / f"data/chat_history/{scope}.jsonl",
            [_record("2026-01-01T00:00:00+08:00", "user", content)],
        )
    plan = inspect_character_data_import(source, target)
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-roles-01",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)
    store = TimelineStore(target / "data/chat_history/timeline.sqlite3")
    assert [entry.payload["text"] for entry in store.read_all("Alpha")] == ["alpha"]
    assert [entry.payload["text"] for entry in store.read_all("Beta")] == ["beta"]


def test_incremental_import_rejects_runtime_v2_only_source(tmp_path: Path) -> None:
    source = tmp_path / "runtime-v2"
    target = tmp_path / "target"
    (source / "data/chat_history").mkdir(parents=True)
    TimelineStore(source / "data/chat_history/timeline.sqlite3").initialize()
    target.mkdir()

    with pytest.raises(LegacyImportError, match="LEGACY_DATA_SOURCE_UNRECOGNIZED"):
        inspect_character_data_import(source, target)


def test_incremental_import_accepts_partial_legacy_jsonl_without_config(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (source / "data/config/system_config.yaml").unlink()
    (source / "data/config").rmdir()
    _write_history(
        source / "data/chat_history/Sakura.jsonl",
        [_record("2026-01-01T00:00:00+08:00", "user", "surviving row")],
    )

    plan = inspect_character_data_import(source, target)

    assert plan["totals"]["historyNew"] == 1
    assert plan["totals"]["recoverableErrors"] == 0


def test_incremental_import_rejects_provably_active_legacy_source(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (source / "data/sakura.lock").write_text(
        f"{os.getpid()}\nsakura\nlocalhost\n",
        encoding="ascii",
    )

    with pytest.raises(LegacyImportError, match="LEGACY_SOURCE_ACTIVE"):
        inspect_character_data_import(source, target)

    (source / "data/sakura.lock").write_bytes(b"damaged-lock\xff")
    assert inspect_character_data_import(source, target)["schemaVersion"] == 1


def test_incremental_plan_is_stale_after_target_conflict_changes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    history = source / "data/chat_history/Sakura.jsonl"
    records = [_record("2026-01-01T00:00:00+08:00", "user", "source")]
    _write_history(history, records)
    initial = inspect_character_data_import(source, target)
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-stale-01",
        plan_token=str(initial["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    records[0]["content"] = "source changed"
    _write_history(history, records)
    conflict = inspect_character_data_import(source, target)
    assert conflict["totals"]["historyConflicts"] == 1
    timeline = target / "data/chat_history/timeline.sqlite3"
    with sqlite3.connect(timeline) as connection:
        connection.execute(
            "UPDATE timeline_entries SET payload_json = ?",
            (json.dumps({"text": "target changed"}),),
        )

    with pytest.raises(LegacyImportError, match="LEGACY_DATA_IMPORT_PLAN_STALE"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-stale-02",
            plan_token=str(conflict["planToken"]),
            overwrite_conflicts=True,
        )


def test_incremental_cross_role_history_identity_is_never_overwritable(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _write_history(
        source / "data/chat_history/Alpha.jsonl",
        [_record("2026-01-01T00:00:00+08:00", "user", "alpha")],
    )
    initial = inspect_character_data_import(source, target)
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-scope-01",
        plan_token=str(initial["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)
    with sqlite3.connect(target / "data/chat_history/timeline.sqlite3") as connection:
        connection.execute("UPDATE timeline_entries SET character_id = 'Beta'")

    blocked = inspect_character_data_import(source, target)
    assert blocked["blocked"] is True
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_SCOPE_CONFLICT"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-scope-02",
            plan_token=str(blocked["planToken"]),
            overwrite_conflicts=True,
        )


def test_incremental_memory_merges_qdrant_history_and_profiles_by_role(tmp_path: Path) -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.models")
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    memory = source / "data/memory"
    qdrant_root = memory / "qdrant"
    client = qdrant_client.QdrantClient(path=str(qdrant_root))
    client.create_collection(
        "sakura_memories",
        vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
    )
    client.upsert(
        "sakura_memories",
        [
            models.PointStruct(
                id="00000000-0000-0000-0000-000000000001",
                vector=[0.0] * 384,
                payload={"user_id": "Alpha", "data": "alpha-memory"},
            ),
            models.PointStruct(
                id="00000000-0000-0000-0000-000000000002",
                vector=[1.0] * 384,
                payload={"user_id": "Beta", "data": "beta-memory"},
            ),
        ],
    )
    client.close()
    (qdrant_root / ".lock").unlink(missing_ok=True)
    with sqlite3.connect(memory / "mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?)",
            ("event-alpha", "00000000-0000-0000-0000-000000000001", "ADD", "Alpha"),
        )
    (memory / "core_profiles.json").write_text(
        json.dumps(
            {
                "Beta": {
                    "content": "beta profile",
                    "metadata": {"scope": "Beta", "layer": "core_profile"},
                }
            }
        ),
        encoding="utf-8",
    )

    plan = inspect_character_data_import(source, target)
    assert {item["characterId"] for item in plan["characters"]} == {"Alpha", "Beta"}
    assert plan["totals"]["memoryNew"] == 4
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-memory-01",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    repeated = inspect_character_data_import(source, target)
    assert repeated["totals"]["memoryNew"] == 0
    assert repeated["totals"]["memoryIdentical"] == 4
    with sqlite3.connect(target / "data/memory/mem0_history.db") as connection:
        assert connection.execute(
            "SELECT user_id FROM history WHERE id = 'event-alpha'"
        ).fetchone() == ("Alpha",)
    profiles = json.loads(
        (target / "data/memory/core_profiles.json").read_text(encoding="utf-8")
    )
    assert set(profiles) == {"Beta"}


def test_incremental_memory_quarantines_unreadable_legacy_sqlite(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (source / "data/memory/mem0_history.db").write_bytes(b"not sqlite")

    plan = inspect_character_data_import(source, target)
    assert plan["totals"]["recoverableErrors"] >= 1
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-dirty-memory-01",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    assert not (target / "data/memory/mem0_history.db").exists()
    assert (
        target
        / "data/legacy-imports/incremental-dirty-memory-01/quarantine/memory/mem0_history.db"
    ).read_bytes() == b"not sqlite"


def test_incremental_unreadable_target_memory_fails_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target_memory = target / "data/memory"
    target_memory.mkdir(parents=True)
    database = target_memory / "mem0_history.db"
    database.write_bytes(b"not a sqlite database")
    before = database.read_bytes()

    with pytest.raises(LegacyImportError, match="LEGACY_DATA_TARGET_MEMORY_INVALID"):
        inspect_character_data_import(source, target)

    assert database.read_bytes() == before
    assert not list(target.glob(".legacy-import-*"))
    assert not (target / "data/legacy-imports").exists()


def test_incremental_memory_plan_is_stale_after_source_changes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    database = source / "data/memory/mem0_history.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('event-1', 'memory-1', 'ADD', 'Alpha')"
        )
    plan = inspect_character_data_import(source, target)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE history SET event = 'UPDATE' WHERE id = 'event-1'")

    with pytest.raises(LegacyImportError, match="LEGACY_DATA_IMPORT_PLAN_STALE"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-memory-stale-01",
            plan_token=str(plan["planToken"]),
            overwrite_conflicts=False,
        )


def test_incremental_cross_role_memory_identity_is_never_overwritable(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target_memory = target / "data/memory"
    target_memory.mkdir(parents=True)
    schema = (
        "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, "
        "event TEXT, user_id TEXT)"
    )
    with sqlite3.connect(source / "data/memory/mem0_history.db") as connection:
        connection.execute(schema)
        connection.execute(
            "INSERT INTO history VALUES ('shared-event', 'source-memory', 'ADD', 'Alpha')"
        )
    with sqlite3.connect(target_memory / "mem0_history.db") as connection:
        connection.execute(schema)
        connection.execute(
            "INSERT INTO history VALUES ('shared-event', 'target-memory', 'ADD', 'Beta')"
        )

    blocked = inspect_character_data_import(source, target)
    assert blocked["blocked"] is True
    assert blocked["totals"]["memoryConflicts"] == 1
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_SCOPE_CONFLICT"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-memory-scope-01",
            plan_token=str(blocked["planToken"]),
            overwrite_conflicts=True,
        )


def test_incremental_history_scope_must_match_referenced_point(tmp_path: Path) -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.models")
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    memory = source / "data/memory"
    point_id = "00000000-0000-0000-0000-000000000011"
    client = qdrant_client.QdrantClient(path=str(memory / "qdrant"))
    client.create_collection(
        "sakura_memories",
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    client.upsert(
        "sakura_memories",
        [
            models.PointStruct(
                id=point_id,
                vector=[0.0] * 4,
                payload={"user_id": "Alpha"},
            )
        ],
    )
    client.close()
    (memory / "qdrant/.lock").unlink(missing_ok=True)
    with sqlite3.connect(memory / "mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('event-cross-role', ?, 'ADD', 'Beta')",
            (point_id,),
        )

    plan = inspect_character_data_import(source, target)
    assert plan["blocked"] is True
    assert plan["totals"]["memoryConflicts"] >= 1
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_SCOPE_CONFLICT"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-source-scope-conflict",
            plan_token=str(plan["planToken"]),
            overwrite_conflicts=True,
        )


def test_incremental_history_scope_must_match_preserved_target_point(
    tmp_path: Path,
) -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.models")
    source = _source(tmp_path)
    target = tmp_path / "target"
    target_memory = target / "data/memory"
    target_memory.mkdir(parents=True)
    point_id = "00000000-0000-0000-0000-000000000015"
    client = qdrant_client.QdrantClient(path=str(target_memory / "qdrant"))
    client.create_collection(
        "sakura_memories",
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    client.upsert(
        "sakura_memories",
        [
            models.PointStruct(
                id=point_id,
                vector=[0.0] * 4,
                payload={"user_id": "Alpha"},
            )
        ],
    )
    client.close()
    (target_memory / "qdrant/.lock").unlink(missing_ok=True)
    with sqlite3.connect(source / "data/memory/mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('event-target-cross-role', ?, 'ADD', 'Beta')",
            (point_id,),
        )

    plan = inspect_character_data_import(source, target)

    assert plan["blocked"] is True
    assert plan["totals"]["memoryConflicts"] == 1
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_SCOPE_CONFLICT"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-target-scope-conflict",
            plan_token=str(plan["planToken"]),
            overwrite_conflicts=True,
        )


def test_incremental_history_uses_one_canonical_row_for_inspect_and_apply(
    tmp_path: Path,
) -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.models")
    source = _source(tmp_path)
    target = tmp_path / "target"
    target_memory = target / "data/memory"
    target_memory.mkdir(parents=True)
    point_id = "00000000-0000-0000-0000-000000000012"

    for memory in (source / "data/memory", target_memory):
        client = qdrant_client.QdrantClient(path=str(memory / "qdrant"))
        client.create_collection(
            "sakura_memories",
            vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
        )
        client.upsert(
            "sakura_memories",
            [
                models.PointStruct(
                    id=point_id,
                    vector=[0.0] * 4,
                    payload={"user_id": "Alpha"},
                )
            ],
        )
        client.close()
        (memory / "qdrant/.lock").unlink(missing_ok=True)

    with sqlite3.connect(source / "data/memory/mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('event-canonical', ?, 'ADD')",
            (point_id,),
        )
    with sqlite3.connect(target_memory / "mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('event-canonical', ?, 'ADD', 'Alpha')",
            (point_id,),
        )
    # The context manager commits without closing; model the stopped Core by
    # releasing the fixture handle before the atomic Memory-tree rename.
    connection.close()

    plan = inspect_character_data_import(source, target)
    assert plan["blocked"] is False
    assert plan["totals"]["memoryConflicts"] == 0
    assert plan["totals"]["memoryIdentical"] == 2
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-canonical-history",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)


def test_incremental_unscoped_history_is_applied_as_quarantine_only(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    with sqlite3.connect(source / "data/memory/mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('event-unscoped', 'missing-point', 'ADD')"
        )

    plan = inspect_character_data_import(source, target)
    assert plan["blocked"] is False
    assert plan["totals"]["memoryNew"] == 0
    assert plan["totals"]["recoverableErrors"] == 1
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-unscoped-history",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    assert not (target / "data/memory/mem0_history.db").exists()
    quarantine = (
        target
        / "data/legacy-imports/incremental-unscoped-history/quarantine/memory/unscoped-history-rows.jsonl"
    )
    record = json.loads(quarantine.read_text(encoding="utf-8"))
    assert record["code"] == "LEGACY_MEMORY_SCOPE_UNRESOLVED"
    assert record["id"] == "event-unscoped"


def test_incremental_unscoped_qdrant_is_quarantined_without_creating_target_collection(
    tmp_path: Path,
) -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.models")
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    memory = source / "data/memory"
    point_id = "00000000-0000-0000-0000-000000000014"
    client = qdrant_client.QdrantClient(path=str(memory / "qdrant"))
    client.create_collection(
        "sakura_memories",
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    client.upsert(
        "sakura_memories",
        [
            models.PointStruct(
                id=point_id,
                vector=[0.0] * 4,
                payload={"data": "cannot be attributed"},
            )
        ],
    )
    client.close()
    (memory / "qdrant/.lock").unlink(missing_ok=True)

    plan = inspect_character_data_import(source, target)
    assert plan["totals"]["memoryNew"] == 0
    assert plan["totals"]["recoverableErrors"] == 1
    _report, pending = run_character_data_import(
        source,
        target,
        import_id="incremental-unscoped-qdrant",
        plan_token=str(plan["planToken"]),
        overwrite_conflicts=False,
    )
    finalize_commit(pending)

    assert not (target / "data/memory/qdrant").exists()
    quarantine = (
        target
        / "data/legacy-imports/incremental-unscoped-qdrant/quarantine/memory/unscoped-qdrant-points.jsonl"
    )
    record = json.loads(quarantine.read_text(encoding="utf-8"))
    assert record["id"] == point_id
    assert record["payload"] == {"data": "cannot be attributed"}


@pytest.mark.parametrize("operation", ["create_collection", "upsert"])
def test_incremental_target_qdrant_write_failure_aborts_instead_of_quarantining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.models")
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    memory = source / "data/memory"
    client = qdrant_client.QdrantClient(path=str(memory / "qdrant"))
    client.create_collection(
        "sakura_memories",
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    client.upsert(
        "sakura_memories",
        [
            models.PointStruct(
                id="00000000-0000-0000-0000-000000000013",
                vector=[0.0] * 4,
                payload={"user_id": "Alpha"},
            )
        ],
    )
    client.close()
    (memory / "qdrant/.lock").unlink(missing_ok=True)
    plan = inspect_character_data_import(source, target)

    real_client = legacy_incremental._qdrant_client

    def failing_client(path: Path):
        opened = real_client(path)
        if ".legacy-import-staging-" in path.as_posix() and "/payload/" in path.as_posix():
            def fail_write(*_args: object, **_kwargs: object) -> None:
                raise OSError("synthetic target write failure")

            setattr(opened, operation, fail_write)
        return opened

    monkeypatch.setattr(legacy_incremental, "_qdrant_client", failing_client)
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_TARGET_MEMORY_WRITE_FAILED"):
        run_character_data_import(
            source,
            target,
            import_id="incremental-target-write-failure",
            plan_token=str(plan["planToken"]),
            overwrite_conflicts=False,
        )
    assert not list(target.glob(".legacy-import-journal-*"))
    assert not (target / "data/legacy-imports/incremental-target-write-failure").exists()
    assert not (target / "data/memory/qdrant").exists()


def test_incremental_public_role_list_is_bounded(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    profiles = {
        f"Role-{index:03d}": {"content": "profile", "metadata": {}}
        for index in range(300)
    }
    (source / "data/memory/core_profiles.json").write_text(
        json.dumps(profiles),
        encoding="utf-8",
    )

    plan = inspect_character_data_import(source, target)
    assert len(plan["characters"]) == 256
    assert plan["charactersTruncated"] is True
    assert plan["totals"]["memoryNew"] == 300


def test_commit_rejects_target_parent_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Junction coverage runs in the Windows release matrix")
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-link-test" / "payload"
    external = tmp_path / "external"
    target.mkdir()
    external.mkdir()
    (target / "config").symlink_to(external, target_is_directory=True)
    (payload / "config").mkdir(parents=True)
    (payload / "config/ui.json").write_text("new", encoding="utf-8")

    with pytest.raises(LegacyImportError, match="LEGACY_COMMIT_TARGET_LINK_UNSUPPORTED"):
        commit_payload(target, "link-test-0001", payload)
    assert not (external / "ui.json").exists()
