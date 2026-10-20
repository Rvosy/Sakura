from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
from app.core_host.plugin_host_services import _TimelineHostService
from plugins.optional.sakura_mem0.memory import MemoryStore
from plugins.optional.sakura_mem0.memory_curator import MemoryCurator
from plugins.optional.sakura_mem0.memory_recall import MemoryRecallService
from plugins.optional.sakura_mem0.domain_types import ContextRequest, ContextMessage, ChatHistoryEntry
from plugins.optional.sakura_mem0.boundary import MemoryBoundary
from plugins.optional.sakura_mem0.support import OperationCancelled


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path)
    yield memory
    memory.close()


def entry(identity="u1", content="以后不用提醒我要睡觉了", role="user", date="2026-10-01T12:00:00+00:00"):
    return ChatHistoryEntry(date, role, content, entry_id=identity, turn_id="t-" + identity,
                            evidence_ready=role in {"user", "observation"})


def add_operation(message, **values):
    return {"op": "add", "content": message.content, "layer": "semantic",
            "evidence": [{"entry_id": message.entry_id, "quote": message.content}], **values}


class Client:
    def __init__(self, operations):
        self.operations = operations
        self.requests = []

    def complete_raw(self, system, messages, **kwargs):
        self.requests.append((system, json.loads(messages[0]["content"])))
        return json.dumps({"operations": self.operations}, ensure_ascii=False)

    def close(self):
        pass


def test_offline_chinese_recall_is_independent_and_can_be_empty(store):
    item = store.create_memory({"content": "上周排查了语音合成卡顿，改成流式播放后恢复。"})["memory"]
    store.create_memory({"content": "那天和用户聊了天气，下午下雨了。"})
    assert store.is_ready() and store.needs_embedding_model_download()
    response = store.search_memory({"query": "语音合成卡顿", "automatic": True})
    assert [hit["id"] for hit in response["memories"]] == [item["id"]]
    assert response["memories"][0]["retrieval"]["matched_terms"]
    assert store.search_memory({"query": "我下在 H8", "automatic": True})["memories"] == []


def test_merge_duplicates_preserves_evidence_and_protected_records(store):
    for i in range(2):
        store.create_memory({"id": f"duplicate-{i}", "content": "用户喝咖啡不加糖", "source": "curation",
                             "evidence": [{"entry_id": f"u{i}", "role": "user", "quote": "咖啡不加糖"}]})
    pinned = store.create_memory({"content": "用户喝咖啡不加糖", "source": "curation", "pinned": True})["memory"]
    manual = store.create_memory({"content": "用户喝咖啡不加糖", "source": "manual"})["memory"]
    different = store.create_memory({"content": "用户喝咖啡要加糖", "source": "curation"})["memory"]
    store.import_records([{"id": "other-memory", "scope": "other", "content": "用户喝咖啡不加糖", "source": "curation"}])
    assert store.merge_duplicates() == {"merged": 1}
    kept = store.get("duplicate-0")
    assert {source["entry_id"] for source in kept["evidence"]} == {"u0", "u1"}
    assert store.get("duplicate-1", include_deleted=True)["deleted"] is True
    assert all(store.get(item["id"]) == item for item in (pinned, manual, different))
    assert any(m["id"] == "other-memory" and not m["deleted"] for m in store.all_records())
    assert store.merge_duplicates() == {"merged": 0}


def test_update_now_processes_below_threshold_and_does_not_replay(tmp_path):
    history = TimelineStore(tmp_path / "timeline.sqlite3")
    history.initialize()
    history.append(NewTimelineEntry("u1", "t1", "sakura", TimelineKind.HUMAN, "chat",
                                   "2026-10-01T12:00:00+00:00", {"text": "用户喝咖啡不加糖"}))
    service = _TimelineHostService(history, lambda: "sakura")
    timeline = SimpleNamespace(read_since=lambda payload: service.call("read_since", [payload]),
                               read_recent=lambda payload: service.call("read_recent", [payload]))
    client = Client([add_operation(entry(content="用户喝咖啡不加糖"))])
    boundary = MemoryBoundary(tmp_path, "sakura", curation_config_getter=lambda: {
        "triggerTurns": 8, "curationModelRef": {"serviceKey": "model", "profileId": "p", "modelId": "m"}},
        model_client_factory=lambda _: client)
    try:
        boundary.note_timeline_changed(timeline)
        assert boundary.list_memories() == []
        boundary.update_now(timeline)
        deadline = time.monotonic() + 5
        while boundary._curation_active and time.monotonic() < deadline:
            time.sleep(.01)
        assert [m["content"] for m in boundary.list_memories()] == ["用户喝咖啡不加糖"]
        assert boundary.settings_get()["curation"]["result"]["created"] == 1
        boundary.update_now(timeline)
        assert len(client.requests) == 1
    finally:
        boundary.close()


def test_dense_and_lexical_union_does_not_let_dense_top_k_hide_chinese_hit(store, monkeypatch):
    lexical = store.create_memory({"content": "Sakura 的麦克风权限已打开"})["memory"]
    dense = store.create_memory({"content": "喝咖啡会选不加糖的拿铁"})["memory"]
    monkeypatch.setattr(store._index, "search", lambda *_: [{"id": dense["id"], "revision": dense["revision"], "score": .8}])
    result = store.search_memory({"query": "Sakura 麦克风权限", "automatic": True})["memories"]
    assert {item["id"] for item in result} == {lexical["id"], dense["id"]}
    store.update_memory({"id": dense["id"], "content": "现在改喝茶"})
    assert dense["id"] not in {item["id"] for item in store.search_memory({"query": "咖啡"})["memories"]}


def test_profile_injection_and_current_query_do_not_carry_previous_topic(store, tmp_path):
    profile = store.create_memory({"content": "用户明确要求以后不用提醒睡觉", "layer": "procedural", "pinned": True})["memory"]
    store.create_memory({"content": "之前排查语音合成卡顿的问题"})
    boundary = MemoryBoundary(tmp_path, "sakura", memory_store=store)
    result = MemoryRecallService(boundary).recall(ContextRequest(
        current_input="我下在 H8", character_id="sakura",
        recent_messages=(ContextMessage("user", "语音合成卡顿是什么原因"),),
    ))
    assert [fragment.metadata["memory_id"] for fragment in result.fragments] == [profile["id"]]
    assert result.fragments[0].metadata["selection"] == "profile"
    boundary.close()


def test_curation_requires_per_item_evidence_and_preserves_negation(store):
    human = entry()
    assistant = entry("a1", "我仍会轻柔提醒，并非严格禁止", "assistant")
    client = Client([
        add_operation(human, layer="procedural", content="可以轻柔提醒睡觉"),
        add_operation(assistant, content="用户希望继续提醒睡觉"),
        {**add_operation(human), "evidence": [{"entry_id": "missing", "quote": "不存在"}]},
    ])
    result = MemoryCurator(client, store).curate_entries([human, assistant], cursor="cursor1")
    memories = store.list_memories(limit=None)
    assert result.created == 1 and result.ignored == 2
    assert memories[0]["content"] == "用户原话：以后不用提醒我要睡觉了"
    assert memories[0]["metadata"]["source_entry_ids"] == ["u1"]
    assert memories[0]["evidence"][0]["quote"] == human.content
    assert store.get_state("curation")["curation_cursor"] == "cursor1"
    assert client.requests[0][1]["messages"][0]["entry_id"] == "u1"
    MemoryCurator(Client([]), store).curate_entries([human, assistant], cursor="cursor2")
    assert len(store.list_memories(limit=None)) == 1


def test_observation_cannot_overwrite_user_fact_but_can_record_activity(store):
    old = store.create_memory({"content": "用户不希望收到睡觉提醒", "layer": "procedural", "occurred_at": "2026-01-01T00:00:00Z"})["memory"]
    observed = entry("o1", "屏幕显示用户正在玩五子棋", "observation")
    operations = [add_operation(observed, op="update", id=old["id"], layer="procedural", content="用户喜欢睡觉提醒"),
                  add_operation(observed, layer="episodic")]
    result = MemoryCurator(Client(operations), store).curate_entries([observed])
    assert store.get(old["id"])["content"] == old["content"]
    assert result.created == 1 and result.ignored == 1


def test_human_edit_during_model_request_is_preserved_without_blocking(store):
    old = store.create_memory({"content": "午饭喜欢面条", "occurred_at": "2026-01-01T00:00:00Z"})["memory"]
    human = entry(content="午饭现在喜欢米饭")
    entered, proceed = threading.Event(), threading.Event()
    client = Client([add_operation(human, op="update", id=old["id"])])
    original = client.complete_raw
    def complete(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return original(*args, **kwargs)
    client.complete_raw = complete
    results = []
    worker = threading.Thread(target=lambda: results.append(MemoryCurator(client, store).curate_entries([human])))
    worker.start()
    assert entered.wait(5)
    store.update_memory({"id": old["id"], "content": "午饭喜欢饺子", "pinned": True})
    proceed.set()
    worker.join(5)
    assert not worker.is_alive()
    assert store.get(old["id"])["content"] == "午饭喜欢饺子"
    assert results[0].ignored == 1


def test_curation_batch_rollback_keeps_cursor_and_fts_consistent(store, monkeypatch):
    human = entry(content="喜欢咖啡，也喜欢茶")
    client = Client([add_operation(human, content="喜欢咖啡"), add_operation(human, content="喜欢茶")])
    save = store._save
    def fail_second(record, **kwargs):
        if record["content"] == "喜欢茶":
            raise sqlite3.OperationalError("disk full")
        return save(record, **kwargs)
    monkeypatch.setattr(store, "_save", fail_second)
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        MemoryCurator(client, store).curate_entries([human], cursor="bad-cursor")
    assert store.list_memories(limit=None) == []
    assert store.get_state("curation", default={}) == {}
    assert store.search_memory({"query": "咖啡"})["memories"] == []
    monkeypatch.setattr(store, "_save", save)
    MemoryCurator(client, store).curate_entries([human], cursor="ok")
    assert len(store.list_memories(limit=None)) == 2


def test_failed_model_and_cancelled_generation_do_not_advance_cursor(store):
    class BrokenClient:
        def complete_raw(self, *_args, **_kwargs):
            return "not JSON"
    with pytest.raises(ValueError):
        MemoryCurator(BrokenClient(), store).curate_entries([entry()], cursor="bad")
    assert store.get_state("curation", default={}) == {}
    def cancelled():
        raise OperationCancelled()
    with pytest.raises(OperationCancelled):
        MemoryCurator(Client([add_operation(entry())]), store).curate_entries([entry()], cursor="bad", cancel_checker=cancelled)
    assert store.list_memories(limit=None) == []


def test_old_history_cannot_overwrite_new_fact_and_deleted_source_stays_deleted(store):
    human = entry(content="我更喜欢拿铁")
    old = store.create_memory({"content": "现在喜欢美式咖啡", "occurred_at": "2026-10-10T00:00:00Z"})["memory"]
    client = Client([add_operation(human, op="update", id=old["id"])])
    assert MemoryCurator(client, store).curate_entries([human]).ignored == 1
    assert store.get(old["id"])["content"] == "现在喜欢美式咖啡"
    fact = entry("u2", "昨天一起修好了语音问题")
    MemoryCurator(Client([add_operation(fact, layer="episodic")]), store).curate_entries([fact])
    event = next(item for item in store.list_memories(limit=None) if item["layer"] == "episodic")
    store.forget_memory({"id": event["id"]})
    MemoryCurator(Client([add_operation(fact, layer="episodic")]), store).curate_entries([fact])
    assert len(store.list_memories(limit=None)) == 1
    assert store.get(event["id"], include_deleted=True)["deleted"]


def test_original_human_evidence_can_correct_a_later_unsupported_legacy_summary(store):
    human = entry(content="以后不用提醒我要睡觉了", date="2026-06-12T00:41:53+08:00")
    store.import_records([{"id": "old", "scope": "sakura", "source": "curated", "layer": "procedural",
                           "content": "仍会轻柔提醒，并非严格禁止", "created_at": "2026-06-25T00:00:00+08:00"}])
    result = MemoryCurator(Client([add_operation(human, op="update", id="old", layer="procedural")]), store).curate_entries([human])
    assert result.updated == 1
    assert store.get("old")["content"] == "用户原话：以后不用提醒我要睡觉了"
    assert store.get("old")["evidence"][0]["quote"] == human.content


def test_role_isolation_restart_and_deleted_export(store, tmp_path):
    item = store.create_memory({"content": "用户喜欢樱花"})["memory"]
    other = MemoryStore(tmp_path, scope_id="other")
    assert other.search_memory({"query": "樱花"})["memories"] == []
    with pytest.raises(ValueError):
        other.update_memory({"id": item["id"], "content": "覆盖"})
    other.close()
    store.forget_memory({"id": item["id"]})
    archive = store.export_records()
    target = MemoryStore(tmp_path / "target")
    target.restore_records(archive)
    target.restore_records([{**archive[0], "metadata": {**archive[0]["metadata"], "deleted": False}}])
    assert target.list_memories(limit=None) == []
    assert target.get(item["id"], include_deleted=True)["deleted"]
    target.close()


def test_legacy_profile_and_history_extra_columns_survive_migration(tmp_path):
    from plugins.optional.sakura_mem0.legacy import normalize_existing_history_database
    root = tmp_path / "data/memory"
    root.mkdir(parents=True)
    profile = "原始档案：不用提醒睡觉。\n生日是 5 月 17 日。"
    (root / "core_profiles.json").write_text(json.dumps({"sakura": {"content": profile,
        "metadata": {"created_at": "2024-01-01", "source": "old-import"}}}), encoding="utf-8")
    db = root / "mem0_history.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE history (id TEXT PRIMARY KEY, extra TEXT)")
        connection.execute("INSERT INTO history VALUES ('h1','keep')")
    normalize_existing_history_database(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT extra FROM history").fetchone()[0] == "keep"
    memory = MemoryStore(tmp_path)
    assert memory.core_profile()["content"] == profile
    assert memory.core_profile()["source"] == "old-import"
    memory.close()
    reopened = MemoryStore(tmp_path)
    assert len(reopened.list_memories(limit=None)) == 1
    reopened.close()


def test_session_completion_stops_automatic_recall_but_remains_searchable(store):
    task = store.create_memory({"content": "正在排查语音卡顿", "layer": "session"})["memory"]
    store.update_memory({"id": task["id"], "state": "completed"})
    assert store.search_memory({"query": "语音卡顿", "automatic": True})["memories"] == []
    assert store.search_memory({"query": "语音卡顿"})["memories"][0]["state"] == "completed"


def test_history_only_migration_uses_latest_text_and_keeps_deletions(tmp_path):
    from contextlib import closing
    root = tmp_path / "data/memory"
    root.mkdir(parents=True)
    with closing(sqlite3.connect(root / "mem0_history.db")) as db, db:
        db.execute("CREATE TABLE history (memory_id TEXT, user_id TEXT, new_memory TEXT, event TEXT, created_at TEXT)")
        db.executemany("INSERT INTO history VALUES (?,?,?,?,?)", [
            ("fact", "sakura", "早上喝咖啡", "ADD", "2026-10-01"),
            ("fact", "sakura", "现在改喝茶", "UPDATE", "2026-10-02"),
            ("gone", "sakura", "旧偏好", "ADD", "2026-10-01"),
            ("gone", "sakura", None, "DELETE", "2026-10-03"),
        ])
    memory = MemoryStore(tmp_path)
    try:
        assert memory.get("fact")["content"] == "现在改喝茶"
        assert memory.get("fact")["created_at"] == "2026-10-01"
        assert memory.get("fact")["updated_at"] == "2026-10-02"
        assert memory.get("gone") is None
        assert memory.get("gone", include_deleted=True)["deleted"]
    finally:
        memory.close()


def test_directory_import_applies_unassigned_deletions_before_old_copies(store):
    store.import_records([
        {"id": "old", "scope": "sakura", "content": "旧事实"},
        {"id": "old", "scope": "", "content": "", "deleted": True},
    ])
    assert store.get("old") is None
    assert store.all_records()[0]["deleted"]


def test_history_range_snapshot_preserves_pagination_and_roles(tmp_path):
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    for i in range(5):
        timeline.append(NewTimelineEntry(f"u{i}", f"t{i}", "sakura", TimelineKind.HUMAN, "chat",
                                        f"2026-10-0{i+1}T12:00:00Z", {"text": f"消息{i}"}))
    service = _TimelineHostService(timeline, lambda: "sakura")
    request = {"startDate": "2026-10-02", "endDate": "2026-10-04", "limit": 2}
    page = service.call("read_range", [request])
    assert [item["entryId"] for item in page["entries"]] == ["u1", "u2"]
    timeline.append(NewTimelineEntry("late", "late", "sakura", TimelineKind.HUMAN, "chat",
                                    "2026-10-03T12:00:00Z", {"text": "晚导入"}))
    final = service.call("read_range", [{**request, "cursor": page["nextCursor"], "snapshotCursor": page["snapshotCursor"]}])
    assert [item["entryId"] for item in final["entries"]] == ["u3"]
    assert not final["hasMore"] and final["total"] == 3


def test_backfill_resumes_after_model_failure_without_duplicate_memories(tmp_path):
    history = TimelineStore(tmp_path / "timeline.sqlite3")
    history.initialize()
    for i in range(40):
        history.append(NewTimelineEntry(f"u{i}", f"t{i}", "sakura", TimelineKind.HUMAN, "chat",
                                        "2026-10-01T12:00:00Z", {"text": f"完成任务{i}"}))
    service = _TimelineHostService(history, lambda: "sakura")
    timeline = SimpleNamespace(read_range=lambda payload: service.call("read_range", [payload]))
    class BackfillClient(Client):
        fail = True
        def complete_raw(self, system, messages, **kwargs):
            messages = json.loads(messages[0]["content"])["messages"]
            self.requests.append(messages)
            if self.fail and len(self.requests) == 2:
                raise RuntimeError("上游连接断开")
            first = messages[0]
            return json.dumps({"operations": [{"op": "add", "layer": "episodic", "content": first["content"],
                "evidence": [{"entry_id": first["entry_id"], "quote": first["content"]}]}]})
    client = BackfillClient([])
    boundary = MemoryBoundary(tmp_path, "sakura",
        curation_config_getter=lambda: {"curationModelRef": {"serviceKey": "model", "profileId": "p", "modelId": "m"}},
        model_client_factory=lambda _: client)
    def await_finished():
        deadline = time.monotonic() + 5
        while boundary._curation_active and time.monotonic() < deadline:
            time.sleep(.01)
        assert not boundary._curation_active
    try:
        boundary.start_backfill(timeline, "2026-10-01", "2026-10-01")
        await_finished()
        assert boundary.settings_get()["backfill"]["status"] == "failed"
        assert len(boundary.list_memories()) == 1
        client.fail = False
        boundary.start_backfill(timeline, "2026-10-01", "2026-10-01")
        await_finished()
        assert boundary.settings_get()["backfill"]["status"] == "completed"
        assert len(boundary.list_memories()) == 2
        assert len(client.requests[-1]) == 8
        assert boundary.memory_store.get_state("curation", default={}) == {}
    finally:
        boundary.close()


def test_history_date_range_follows_local_calendar(tmp_path):
    from datetime import datetime, timezone
    history = TimelineStore(tmp_path / "timeline.sqlite3")
    history.initialize()
    for entry_id, local in (("previous", datetime(2026, 10, 1, 23, 30)),
                            ("selected", datetime(2026, 10, 2, 0, 30))):
        timestamp = local.astimezone().astimezone(timezone.utc).isoformat()
        history.append(NewTimelineEntry(entry_id, entry_id, "sakura", TimelineKind.HUMAN, "chat",
                                        timestamp, {"text": "跨午夜的历史"}))
    entries, _, _, _, total = history.read_range("sakura", "2026-10-02", "2026-10-02")
    assert [entry.entry_id for entry in entries] == ["selected"]
    assert total == 1


def test_cancel_backfill_does_not_commit_a_late_model_result(tmp_path):
    entered, resume = threading.Event(), threading.Event()
    history = TimelineStore(tmp_path / "timeline.sqlite3")
    history.initialize()
    history.append(NewTimelineEntry("u1", "t1", "sakura", TimelineKind.HUMAN, "chat",
                                    "2026-10-01T12:00:00Z", {"text": "喜欢拿铁"}))
    service = _TimelineHostService(history, lambda: "sakura")
    class SlowClient(Client):
        def complete_raw(self, *args, **kwargs):
            entered.set()
            assert resume.wait(5)
            return super().complete_raw(*args, **kwargs)
    boundary = MemoryBoundary(tmp_path, "sakura",
        curation_config_getter=lambda: {"curationModelRef": {"serviceKey": "model", "profileId": "p", "modelId": "m"}},
        model_client_factory=lambda _: SlowClient([add_operation(entry("u1", "喜欢拿铁"))]))
    try:
        boundary.start_backfill(SimpleNamespace(read_range=lambda payload: service.call("read_range", [payload])),
                                "2026-10-01", "2026-10-01")
        assert entered.wait(5)
        boundary.cancel_backfill()
        resume.set()
        deadline = time.monotonic() + 5
        while boundary._curation_active and time.monotonic() < deadline:
            time.sleep(.01)
        assert boundary.settings_get()["backfill"]["status"] == "cancelled"
        assert boundary.list_memories() == []
        assert not boundary.settings_get()["backfill"].get("curation_cursor")
    finally:
        resume.set()
        boundary.close()
