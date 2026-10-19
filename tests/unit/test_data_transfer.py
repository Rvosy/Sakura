from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core_host.history import HistoryBoundary
from app.plugin_sdk.sakura_data_transfer import read_archive, write_archive
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore, timeline_record
from plugins.optional.sakura_mem0.boundary import _project_timeline_entries, _curation_evidence_turns
from plugins.optional.sakura_mem0.memory import MemoryRecord, MemoryStore


def entry(identity, date, *, turn=None, kind=TimelineKind.HUMAN):
    payload = {"text": "你好\n第二行：不是另一条消息"}
    if kind == TimelineKind.ASSISTANT:
        payload = {"segments": [{"text": "こんにちは", "translation": "你好", "tone": "中性",
                                  "portrait": "neutral", "suppressTts": False}]}
    return NewTimelineEntry(identity, turn or identity, "sakura", kind, "chat", date, payload)


def history(root):
    store = TimelineStore(StoragePaths(root).timeline_database())
    store.initialize()
    boundary = HistoryBoundary("gen", "credential", root,
        character_provider=lambda: "sakura")
    return store, boundary


def test_history_roundtrip_merge_and_text(tmp_path):
    source, exporter = history(tmp_path / "source")
    original = entry("old", "2024-01-02T10:00:00+08:00")
    reply = entry("reply", "2024-01-02T10:00:01+08:00", turn="old", kind=TimelineKind.ASSISTANT)
    source.append_many([original, reply])
    file = tmp_path / "chat.json"
    request = {"expectedCharacterId": "sakura", "expectedGenerationId": "gen", "path": str(file), "format": "json", "action": "export"}
    assert exporter._transfer(request)["exported"] == 2
    target, importer = history(tmp_path / "target")
    target.append(entry("recent", "2026-01-01T00:00:00+00:00"))
    before = target.latest_cursor("sakura")
    assert importer._transfer({**request, "action": "preview"})["added"] == 2
    assert len(target.read_all("sakura")) == 1
    assert importer._transfer({**request, "action": "import"})["added"] == 2
    assert importer._transfer({**request, "action": "import"})["identical"] == 2
    assert timeline_record(target.get_entry("sakura", "reply")) == timeline_record(source.get_entry("sakura", "reply"))
    page, cursor, more, total = target.read_page_before("sakura", 1)
    assert [item.entry_id for item in page] == ["recent"]
    page, cursor, more, total = target.read_page_before("sakura", 1, before_cursor=cursor)
    assert [item.entry_id for item in page] == ["reply"]
    page, _, more, total = target.read_page_before("sakura", 1, before_cursor=cursor)
    assert [item.entry_id for item in page] == ["old"]
    assert not more and total == 3
    page = target.read_turn_page("sakura", category="conversation", limit=1,
        observation_since=datetime.now(timezone.utc), proactive_since=datetime.now(timezone.utc))
    assert page.turns[0][0].entry_id == "recent"
    earlier = target.read_turn_page("sakura", category="conversation", limit=1,
        observation_since=datetime.now(timezone.utc), proactive_since=datetime.now(timezone.utc),
        before_cursor=page.next_cursor, snapshot_cursor=page.snapshot_cursor)
    assert [item.entry_id for item in earlier.turns[0]] == ["old", "reply"]
    changes, _, _ = target.read_since("sakura", before, 50)
    assert [item.entry_id for item in changes] == ["old", "reply"]
    assert all(item.payload["imported"] for item in changes)
    txt = tmp_path / "chat.txt"
    exporter._transfer({**request, "path": str(txt), "format": "txt"})
    text = txt.read_text(encoding="utf-8")
    assert original.payload["text"] in text and "こんにちは" in text and "译文：你好" in text


def test_history_invalid_batch_keeps_existing_and_conflicts_keep_local(tmp_path):
    store, _ = history(tmp_path)
    store.append(entry("existing", "2026-01-01T00:00:00Z"))
    original = [timeline_record(item) for item in store.read_all("sakura")]
    changed = {**original[0], "payload": {"text": "修改过的外部记录"}}
    assert store.import_records("sakura", [changed], preview=False)["conflicts"] == 1
    invalid = {**changed, "entry_id": "bad", "created_at": "not a date"}
    with pytest.raises(ValueError):
        store.import_records("sakura", [timeline_record(entry("new", "2025-01-01T00:00:00Z")), invalid], preview=False)
    assert [timeline_record(item) for item in store.read_all("sakura")] == original


def test_copied_memory_references_match_copied_chat_and_ids_survive_retries(tmp_path):
    ids = tmp_path / "identities.sqlite3"
    chat = tmp_path / "chat.json"
    memory = tmp_path / "memory.json"
    write_archive(chat, kind="history", provider="sakura.timeline", character_id="a", records=[{
        "entry_id": "entry-a", "turn_id": "turn-a", "kind": "human", "origin": "chat",
        "created_at": "2025-01-01T00:00:00Z", "payload": {"text": "喜欢咖啡"},
    }])
    write_archive(memory, kind="memory", provider="sakura.memory.mem0", character_id="a", records=[{
        "id": "memory-a", "scope": "a", "content": "喜欢咖啡", "layer": "semantic",
        "metadata": {"scope": "a", "source_entry_ids": ["entry-a"], "source_turn_id": "turn-a"},
    }])
    copied = read_archive(memory, kind="memory", provider="sakura.memory.mem0", character_id="b", identity_path=ids)
    chat_copy = read_archive(chat, kind="history", provider="sakura.timeline", character_id="b", identity_path=ids)
    record = copied["records"][0]
    assert record["scope"] == record["metadata"]["scope"] == "b"
    assert record["metadata"]["source_entry_ids"] == [chat_copy["records"][0]["entry_id"]]
    assert record["metadata"]["source_turn_id"] == chat_copy["records"][0]["turn_id"]
    assert copied == read_archive(memory, kind="memory", provider="sakura.memory.mem0", character_id="b", identity_path=ids)
    original = read_archive(memory, kind="memory", provider="sakura.memory.mem0", character_id="a")
    assert original["records"][0]["id"] == "memory-a"


def test_imported_history_does_not_hide_pending_local_curation():
    records = [{"entryId": "imported", "characterId": "sakura", "payload": {"imported": True}}]
    for identity, kind in [("human", "human"), ("assistant", "assistant")]:
        value = entry(identity, "2026-01-01T00:00:00Z", turn="local", kind=TimelineKind(kind))
        records.append({"entryId": identity, "turnId": "local", "characterId": "sakura",
                        "kind": kind, "origin": "chat", "createdAt": value.created_at, "payload": value.payload})
    evidence, count = _curation_evidence_turns(_project_timeline_entries(records, "sakura"))
    assert count == 1
    assert [item.entry_id for item in evidence] == ["human", "assistant"]


def test_mem0_restore_preserves_identity_metadata_and_core_profile():
    stored, profiles, embedded = {}, {}, []
    def insert(*, vectors, ids, payloads):
        stored.update(zip(ids, payloads))
    def embed(content, action):
        embedded.append(content)
        return [0.5, 0.5]
    def get_all(*, filters, top_k):
        return {"results": [{"id": identity, "memory": data["data"], "created_at": data["created_at"],
                             "updated_at": data["updated_at"], "hash": None,
                             "metadata": {key: value for key, value in data.items()
                                          if key not in {"data", "created_at", "updated_at", "user_id"}}}
                            for identity, data in stored.items()][:top_k]}
    backend = SimpleNamespace(embedding_model=SimpleNamespace(embed=embed),
                              vector_store=SimpleNamespace(insert=insert), get_all=get_all)
    store = object.__new__(MemoryStore)
    store.scope_id = "sakura"
    store._get_memory = lambda: backend
    store._load_core_profiles = lambda: dict(profiles)
    store._save_core_profiles = lambda data: profiles.update(data)
    records = [MemoryRecord(id=identity, content=content, layer=layer, scope="sakura",
                           created_at="2024-01-01T00:00:00Z", updated_at="2024-02-01T00:00:00Z",
                           metadata={"source_entry_ids": ["human-1"]}).to_dict()
               for identity, content, layer in [("memory-1", "喜欢咖啡", "semantic"),
                                                 ("core_profile:sakura", "用户是程序员", "core_profile")]]
    store.restore_records(records)
    assert embedded == ["喜欢咖啡"]
    assert stored["memory-1"]["data"] == "喜欢咖啡"
    assert stored["memory-1"]["created_at"] == "2024-01-01T00:00:00Z"
    assert stored["memory-1"]["updated_at"] == "2024-02-01T00:00:00Z"
    assert stored["memory-1"]["source_entry_ids"] == ["human-1"]
    assert profiles["sakura"]["content"] == "用户是程序员"
    assert profiles["sakura"]["metadata"]["updated_at"] == "2024-02-01T00:00:00Z"
    exported = {item["id"]: item for item in store.export_records()}
    for record in records:
        assert exported[record["id"]] == {key: value for key, value in record.items() if key not in {"memory", "score"}}


def test_import_missing_turn_start_preserves_old_snapshot_and_restores_turn_order(tmp_path):
    store, _ = history(tmp_path)
    store.append(entry("reply", "2026-01-01T10:00:01Z", turn="turn", kind=TimelineKind.ASSISTANT))
    store.append(entry("other", "2026-01-02T10:00:00Z"))
    cutoff = datetime.now(timezone.utc)
    snapshot = store.read_turn_page("sakura", category="conversation", limit=1,
                                   observation_since=cutoff, proactive_since=cutoff)
    store.import_records("sakura", [timeline_record(entry("question", "2026-01-01T10:00:00Z", turn="turn"))], preview=False)
    old = store.read_turn_page("sakura", category="conversation", limit=10, observation_since=cutoff,
                              proactive_since=cutoff, snapshot_cursor=snapshot.snapshot_cursor)
    assert [[item.entry_id for item in turn] for turn in old.turns] == [["other"]]
    current = store.read_turn_page("sakura", category="conversation", limit=10,
                                  observation_since=cutoff, proactive_since=cutoff)
    assert [[item.entry_id for item in turn] for turn in current.turns] == [["other"], ["question", "reply"]]
