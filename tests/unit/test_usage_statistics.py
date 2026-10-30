from datetime import datetime, timedelta
import json
import sqlite3
import shutil
from types import SimpleNamespace

import pytest

from app.core_host.plugin_hooks import HookHostService
from app.core_host.usage_statistics import UsageStatisticsBoundary
from app.core_host.plugin_host_services import _UsageHostService
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_LOG_METADATA
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
from app.storage.usage import UsageStore, token_usage
from sakura_assistant.agent.trace import AgentTraceRecorder


@pytest.mark.parametrize("usage, expected", [
    ({"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 80},
      "completion_tokens_details": {"reasoning_tokens": 5}}, (100, 20, 120, 80, 5)),
    ({"input_tokens": 100, "output_tokens": 20, "total_tokens": 130}, (100, 20, 130, None, None)),
    ({"total_tokens": 40}, (None, None, 40, None, None)),
    ({"prompt_tokens": 100}, (100, None, None, None, None)),
    ({}, (None, None, None, None, None)),
    ({"prompt_tokens": 0, "completion_tokens": 0}, (0, 0, 0, None, None)),
])
def test_provider_usage_keeps_unknown_values_and_does_not_add_cache_twice(usage, expected):
    assert token_usage(usage) == expected


def make_trace(root, when):
    recorder = AgentTraceRecorder(root / "data/logs", now=lambda: when)
    with recorder.operation("old-chat", finalize_external=True):
        call = recorder.start_model_call(model="old-model", payload={"messages": []}, prompt_provenance=())
        recorder.record_model_reply(call, raw_message={"content": "Token 用量\n  提示词 tokens : 999999"},
                                    usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    return recorder.path


def test_history_import_survives_rotation_and_live_records_are_not_duplicated(tmp_path):
    yesterday = datetime.now().astimezone() - timedelta(days=1)
    trace = make_trace(tmp_path, yesterday)
    shutil.copyfile(trace, trace.with_name(f"sakura-agent-trace.{yesterday.date()}.1.log"))
    store = UsageStore(tmp_path)
    metadata = {"operationId": "live-1", "model": {"modelId": "new-model"}}
    store.record_response({"usage": {"input_tokens": 50, "output_tokens": 10}}, metadata)
    store.record_response({"usage": {"input_tokens": 50, "output_tokens": 10}}, metadata)
    store.record_response({}, {**metadata, "operationId": "unknown"})
    make_trace(tmp_path, datetime.now().astimezone() + timedelta(seconds=2))
    result = store.snapshot("all")
    assert result["summary"]["calls"] == 3
    assert result["summary"]["measuredCalls"] == 2
    assert result["summary"]["totalTokens"] == 180
    assert result["summary"]["historicalCalls"] == 1
    assert store.snapshot("today")["summary"]["totalTokens"] == 60
    for path in (tmp_path / "data/logs").glob("*.log"):
        path.unlink()
    assert UsageStore(tmp_path).snapshot("all")["summary"]["totalTokens"] == 180


def test_provider_response_is_recorded_and_write_failure_does_not_break_reply(tmp_path):
    store = UsageStore(tmp_path)
    service = HookHostService(None, None, None, usage_store=store)
    data = {"response": {"usage": {"total_tokens": 9}, "message": {"content": "hello"}}}
    assert service.run("model.after_response", data, {"operationId": "one", "model": {"modelId": "model"}}) == data
    assert store.snapshot("all")["summary"]["totalTokens"] == 9
    class BrokenStore:
        def record_response(self, *args):
            raise OSError("disk full")
    service = HookHostService(None, None, None, usage_store=BrokenStore())
    assert service.run("model.after_response", data, {}) == data


def test_plugin_usage_is_owned_deduplicated_and_filtered_by_inclusive_dates(tmp_path):
    store = UsageStore(tmp_path)
    service = _UsageHostService(store)
    records = [
        {"recordId": "start", "category": "summary", "categoryLabel": "摘要", "timestamp": "2026-10-01T00:00:00", "usage": {"total_tokens": 10}},
        {"recordId": "end", "category": "memory_curation", "timestamp": "2026-10-02T23:59:59.999", "usage": {"total_tokens": 20}},
        {"recordId": "outside", "timestamp": "2026-10-03T00:00:00", "usage": {"total_tokens": 30}},
    ]
    for plugin_id in ("plugin.a", "plugin.b"):
        caller = HOST_CALLER.set(plugin_id)
        metadata = HOST_CALLER_LOG_METADATA.set((plugin_id, ()))
        try:
            for record in records:
                assert service.call("record", [{**record, "pluginId": "spoofed"}]) == {"recorded": True}
                assert service.call("record", [record]) == {"recorded": False}
        finally:
            HOST_CALLER_LOG_METADATA.reset(metadata)
            HOST_CALLER.reset(caller)
    result = store.snapshot("custom", start_date="2026-10-01", end_date="2026-10-02")
    assert result["summary"]["totalTokens"] == 60
    assert {row["id"]: row["totalTokens"] for row in result["categories"]} == {
        "plugin.a:summary": 10, "plugin.b:summary": 10, "memory_curation": 40}
    assert {row["id"] for row in result["plugins"]} == {"plugin.a", "plugin.b"}


def test_upgrade_preserves_old_totals_and_enriches_trace_categories(tmp_path):
    from app.storage.usage import trace_replies
    yesterday = datetime.now().astimezone() - timedelta(days=1)
    trace = make_trace(tmp_path, yesterday)
    record = next(trace_replies(trace, {}))
    identity = "trace:" + json.dumps([record["request_time"], record["trace"], record["call"], record["model"]])
    store = UsageStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.executescript("""
            CREATE TABLE usage_calls (id TEXT PRIMARY KEY, at REAL, model TEXT, input_tokens INTEGER,
                output_tokens INTEGER, total_tokens INTEGER, cached_tokens INTEGER, reasoning_tokens INTEGER, source TEXT);
            CREATE TABLE usage_meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO usage_meta VALUES ('imported', '0');
        """)
        db.execute("INSERT INTO usage_meta VALUES ('started', ?)", (str(datetime.now().timestamp()),))
        db.execute("INSERT INTO usage_calls VALUES (?, ?, 'old-model', 100, 20, 120, NULL, NULL, 'trace')", (identity, yesterday.timestamp()))
        db.execute("INSERT INTO usage_calls VALUES ('existing', ?, 'old-live', NULL, NULL, 77, NULL, NULL, 'live')", (yesterday.timestamp(),))
    result = store.snapshot("all")
    assert result["summary"]["calls"] == 2
    assert result["summary"]["totalTokens"] == 197
    assert {row["id"]: row["totalTokens"] for row in result["categories"]} == {"conversation": 120, "uncategorized": 77}
    trace.unlink()
    assert UsageStore(tmp_path).snapshot("all")["summary"]["totalTokens"] == 197


def test_model_client_records_default_and_per_call_function_categories(tmp_path):
    from sakura_model_client import ModelClient
    store = UsageStore(tmp_path)
    hooks = HookHostService(None, None, None, usage_store=store)
    class Provider:
        identity = {"providerId": "fixture", "scopeId": "scope"}
        def invoke(self, method, *args, **kwargs):
            if method == "begin":
                return {"operationId": args[0]["operationId"]}
            if method == "describe":
                return {"contextWindowTokens": 32768}
            if method == "poll":
                return {"state": "succeeded", "sequence": 1}
            if method == "result":
                return {"response": {"message": {"content": "ok"}, "usage": {"total_tokens": 10}}}
            return {}
    context = SimpleNamespace(bind=lambda key: Provider(), get=lambda key: hooks if key == "sakura.host.hooks" else None)
    client = ModelClient(context, {"serviceKey": "fixture", "profileId": "p", "modelId": "m"}, usage_category="memory_curation")
    try:
        client.complete({"messages": []})
        client.complete({"messages": []}, usage_category="vision")
    finally:
        client.close()
    assert {row["id"]: row["totalTokens"] for row in store.snapshot("all")["categories"]} == {"memory_curation": 10, "vision": 10}


def test_statistics_counts_human_turns_across_characters_without_counting_reply_segments(tmp_path):
    timeline = TimelineStore(StoragePaths(tmp_path).timeline_database())
    timeline.initialize()
    now = datetime.now().astimezone()
    rows = [
        ("h1", "turn1", "sakura", TimelineKind.HUMAN, {"text": "one"}),
        ("h2", "turn1", "sakura", TimelineKind.HUMAN, {"text": "two"}),
        ("h3", "turn2", "other", TimelineKind.HUMAN, {"text": "three"}),
        ("a1", "turn1", "sakura", TimelineKind.ASSISTANT, {"segments": [
            {"text": "你好", "translation": "", "tone": "中性", "portrait": "neutral", "suppressTts": False},
            {"text": "再见", "translation": "", "tone": "中性", "portrait": "neutral", "suppressTts": False}]}),
    ]
    for identity, turn, character, kind, payload in rows:
        timeline.append(NewTimelineEntry(identity, turn, character, kind, "chat", now.isoformat(), payload))
    boundary = UsageStatisticsBoundary("generation", "credential", tmp_path)
    request = {"generationId": "generation", "generationCredential": "credential", "name": "usage.statistics.get", "id": "1", "payload": {"period": "today"}}
    result = boundary.handle(request)
    assert result["ok"]
    assert result["payload"]["conversation"] == {"turns": 2, "messages": 3, "characters": 2,
        "daily": [{"day": now.date().isoformat(), "turns": 2}],
        "hourly": [{"hour": now.strftime("%Y-%m-%dT%H:00:00"), "turns": 2}]}
    yesterday = now - timedelta(days=1)
    timeline.append(NewTimelineEntry("h4", "turn1", "sakura", TimelineKind.HUMAN, "chat", yesterday.isoformat(), {"text": "earlier"}))
    all_turns = timeline.usage_summary(None, (now + timedelta(seconds=1)).isoformat())
    assert all_turns["turns"] == 2
    assert all_turns["daily"] == [{"day": yesterday.date().isoformat(), "turns": 1}, {"day": now.date().isoformat(), "turns": 1}]
    assert boundary.handle({**request, "payload": {"period": "bad"}})["ok"] is False
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle({**request, "generationCredential": "expired"})


def test_single_day_groups_actual_usage_and_human_turns_by_hour(tmp_path):
    store = UsageStore(tmp_path)
    for index, (when, tokens) in enumerate([
        ("2026-10-28T00:00:00", 10), ("2026-10-28T00:59:59", 20),
        ("2026-10-28T01:00:00", 30), ("2026-10-28T02:30:00", None),
        ("2026-10-28T23:59:59", 40), ("2026-10-29T00:00:00", 999),
    ]):
        store.record({"recordId": str(index), "timestamp": when,
                      "usage": {"total_tokens": tokens} if tokens is not None else {}},
                     plugin_id="fixture", plugin_name="fixture")
    timeline = TimelineStore(StoragePaths(tmp_path).timeline_database())
    timeline.initialize()
    for identity, turn, character, hour in [
        ("1", "first", "sakura", "00:59:59"), ("2", "first", "sakura", "01:00:00"),
        ("3", "second", "sakura", "01:10:00"), ("4", "third", "other", "01:30:00"),
    ]:
        timeline.append(NewTimelineEntry(identity, turn, character, TimelineKind.HUMAN, "chat",
                                         datetime.fromisoformat("2026-10-28T" + hour).astimezone().isoformat(), {"text": "hello"}))
    boundary = UsageStatisticsBoundary("g", "c", tmp_path)
    result = boundary.handle({"generationId": "g", "generationCredential": "c", "id": "hours",
        "name": "usage.statistics.get", "payload": {"period": "custom", "startDate": "2026-10-28", "endDate": "2026-10-28"}})["payload"]
    assert result["granularity"] == "hour"
    assert result["summary"]["totalTokens"] == 100
    assert [(row["hour"], row["totalTokens"]) for row in result["hourly"]] == [
        ("2026-10-28T00:00:00", 30), ("2026-10-28T01:00:00", 30),
        ("2026-10-28T02:00:00", None), ("2026-10-28T23:00:00", 40)]
    assert result["conversation"]["turns"] == 3
    assert result["conversation"]["hourly"] == [
        {"hour": "2026-10-28T00:00:00", "turns": 1}, {"hour": "2026-10-28T01:00:00", "turns": 2}]
    longer = store.snapshot("custom", start_date="2026-10-28", end_date="2026-10-29")
    assert longer["granularity"] == "day"
    assert [(row["day"], row["totalTokens"]) for row in longer["daily"]] == [("2026-10-28", 100), ("2026-10-29", 999)]
