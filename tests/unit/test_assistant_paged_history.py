from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path

import pytest

from app.core_host.plugin_artifacts import PluginArtifactStore
from app.core_host.plugin_host_services import HostServiceError, _TimelineHostService
from app.plugins.host_services import HOST_CALLER
from app.plugins.sakura_plugin_sdk import _encode_json
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
from sakura_context import ContextRequest
from sakura_cancellation import OperationCancelled
from sakura_assistant.history import PagedHistory
from sakura_assistant.llm.prompts.runtime import ContextPolicy


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
OWNER = "sakura.assistant"


def entry(index: str, turn: str, *, kind: TimelineKind = TimelineKind.HUMAN,
          origin: str = "chat", created_at: datetime = NOW, text: str = "hello",
          semantic: bool = False) -> NewTimelineEntry:
    payload = {"text": text}
    if semantic:
        payload["visual"] = {"analysisStatus": "succeeded", "capturedAt": created_at.isoformat(), "imageCount": 1}
    if origin == "host":
        payload["sourcePluginId"] = "sakura.screen_awareness"
    if kind is TimelineKind.ASSISTANT:
        payload = {"segments": [{"text": text, "translation": "", "tone": "neutral",
                                 "portrait": "", "suppressTts": False}]}
    return NewTimelineEntry(index, turn, "sakura", kind, origin, created_at.isoformat(), payload)


class TimelineProxy:
    def __init__(self, host):
        self.host = host
        self.requests = []
        self.responses = []

    def read_turn_page(self, request):
        token = HOST_CALLER.set(OWNER)
        try:
            response = self.host.call("read_turn_page", [request])
            _encode_json(response)  # The real RPC's frame limit applies here.
            self.requests.append(request)
            self.responses.append(response)
            return response
        finally:
            HOST_CALLER.reset(token)


class ArtifactProxy:
    def __init__(self, store):
        self.store = store

    def resolve(self, artifact_id):
        artifact = self.store.resolve_committed(OWNER, artifact_id)
        return {"path": str(artifact.path), "mediaType": artifact.media_type, "byteLength": artifact.byte_length}

    def release_received(self, artifact_id):
        return self.store.release(OWNER, artifact_id)


def history_for(tmp_path, entries):
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append_many(entries)
    artifacts = PluginArtifactStore(tmp_path, "generation-test")
    host = _TimelineHostService(store, lambda: "different-current-character", artifacts)
    grant = host.grant(OWNER, "sakura")
    proxy = TimelineProxy(host)
    history = PagedHistory(proxy, "sakura", grant["snapshotCursor"], NOW,
                           ArtifactProxy(artifacts), grant["historyToken"])
    return store, artifacts, host, proxy, history, grant


def select(history, budget):
    return ContextPolicy(total_budget=budget).select(
        ContextRequest(), (), history_source=history.source(), projected_drops=history.projected_drops,
    )


def test_real_paged_history_replays_cache_and_reads_more_only_for_a_larger_budget(tmp_path):
    text = "x" * 2_048
    store, _, _, proxy, history, _ = history_for(
        tmp_path, [entry(str(index), str(index), text=text) for index in range(2_000)],
    )
    cost = next(iter(history.source().conversations)).estimated_tokens
    initial = select(history, cost * 10)
    assert [turn.turn_id for turn in initial.selected_turns] == [str(index) for index in range(1_990, 2_000)]
    assert len([request for request in proxy.requests if request["category"] == "conversation"]) == 1
    store.append(entry("late", "late"))
    larger = select(history, cost * 20)
    assert [turn.turn_id for turn in larger.selected_turns] == [str(index) for index in range(1_980, 2_000)]
    assert len([request for request in proxy.requests if request["category"] == "conversation"]) == 2
    requests = len(proxy.requests)
    assert select(history, cost * 10).selected_turns == initial.selected_turns
    assert len(proxy.requests) == requests
    messages = history.messages(initial)
    assert len(messages) == 20
    assert all(message["role"] == "system" and NOW.isoformat() in message["content"] for message in messages[::2])
    assert all(message["role"] == "user" and message["content"] == text for message in messages[1::2])


def test_large_complete_turn_uses_artifact_and_releases_it_after_reading(tmp_path):
    entries = [entry("human", "large")]
    entries.extend(entry(f"system-{index}", "large", kind=TimelineKind.SYSTEM, text="x" * 65_536) for index in range(20))
    _, artifacts, _, proxy, history, _ = history_for(tmp_path, entries)
    snapshot = select(history, 1_000_000)
    assert [turn.turn_id for turn in snapshot.selected_turns] == ["large"]
    messages = history.messages(snapshot)
    assert len(messages) == 22
    assert messages[-1]["content"] == "[Host fact] " + "x" * 65_536
    assert any("artifact" in response for response in proxy.responses)
    assert artifacts.count == 0


def test_multiple_large_turns_use_bounded_pages_without_splitting_or_losing_them(tmp_path):
    _, artifacts, _, proxy, history, _ = history_for(
        tmp_path, [entry(str(index), str(index), text="x" * 65_536) for index in range(20)],
    )
    snapshot = select(history, 1_000_000)
    assert [turn.turn_id for turn in snapshot.selected_turns] == [str(index) for index in range(20)]
    pages = [response for request, response in zip(proxy.requests, proxy.responses) if request["category"] == "conversation"]
    assert len(pages) >= 2
    assert all("artifact" not in page for page in pages)
    assert artifacts.count == 0


@pytest.mark.parametrize("origin", ["scheduled_screen", "host"])
def test_proactive_limit_ttl_and_observation_reply_do_not_duplicate_context(tmp_path, origin):
    entries = [entry("old", "old", kind=TimelineKind.ASSISTANT, origin="proactive", created_at=NOW - timedelta(hours=2))]
    entries.extend(entry(f"proactive-{index}", f"proactive-{index}", kind=TimelineKind.ASSISTANT, origin="proactive", text=f"proactive {index}") for index in range(5))
    entries.extend([
        entry("observation", "observation", kind=TimelineKind.OBSERVATION, origin=origin, semantic=True, text="semantic observation"),
        entry("observed-reply", "observation", kind=TimelineKind.ASSISTANT, origin="proactive", text="observed reply"),
    ])
    _, _, _, _, history, _ = history_for(tmp_path, entries)
    proactive = history.proactive_messages()
    assert len(proactive) == 1
    content = proactive[0]["content"]
    assert "proactive 0" not in content and "proactive 1" not in content
    assert content.index("proactive 2") < content.index("proactive 3") < content.index("proactive 4")
    assert "observed reply" not in content
    selected = select(history, 10_000)
    assert [turn.turn_id for turn in selected.selected_turns] == ["observation"]
    assert [message["role"] for message in history.messages(selected)] == ["system", "assistant"]
    assert len(history.projected_drops) == 5


def test_plugin_visual_history_filters_invalid_and_expired_summaries_and_keeps_snapshot(tmp_path):
    def observation(name, **kwargs):
        return entry(name, name, kind=TimelineKind.OBSERVATION, origin="host", semantic=True, **kwargs)

    valid = observation("valid", text="画面摘要：正在修改角色界面。")
    no_source = replace(observation("no-source"), payload={"text": "unattributed", "visual": valid.payload["visual"]})
    # Unsuccessful analysis leaves only capture metadata; no failed semantic record is stored.
    unanalyzed = replace(observation("unanalyzed"), payload={**valid.payload, "visual": {
        "imageCount": 1, "capturedAt": NOW.isoformat(),
    }})
    store, _, _, _, history, _ = history_for(tmp_path, [
        valid,
        observation("boundary", created_at=NOW - timedelta(hours=2)),
        observation("expired", created_at=NOW - timedelta(hours=2, seconds=1)),
        no_source, unanalyzed,
        entry("trigger", "trigger", kind=TimelineKind.OBSERVATION, origin="host"),
        replace(observation("other-character"), character_id="other"),
    ])
    bounds = {"observation_since": NOW - timedelta(hours=2), "proactive_since": NOW - timedelta(hours=1)}
    assert {row.turn_id for row in store.read_context_candidates("sakura", **bounds)} == {"valid", "boundary"}
    store.append(observation("late"))
    snapshot = select(history, 10_000)
    assert {turn.turn_id for turn in snapshot.selected_turns} == {"valid", "boundary"}
    messages = history.messages(snapshot)
    assert all(message["role"] == "system" for message in messages)
    assert any("正在修改角色界面" in message["content"] for message in messages)
    assert all("不是用户输入" in message["content"] for message in messages)


def test_corrupt_projected_turn_is_diagnosed_without_preventing_older_valid_turn(tmp_path):
    _, _, _, _, history, _ = history_for(tmp_path, [
        entry("valid", "valid"), entry("broken-a", "broken"), entry("broken-b", "broken"),
    ])
    snapshot = select(history, 100)
    assert [turn.turn_id for turn in snapshot.selected_turns] == ["valid"]
    assert [(turn.turn_id, turn.drop_reason) for turn in snapshot.dropped_turns] == [("broken", "corrupt_or_empty")]


@pytest.mark.parametrize("change", ["caller", "character", "snapshot", "revoke"])
def test_frozen_history_authority_rejects_mismatched_or_revoked_requests(tmp_path, change):
    _, _, host, _, _, grant = history_for(tmp_path, [entry("human", "chat")])
    request = {"characterId": "sakura", "snapshotCursor": grant["snapshotCursor"],
               "historyToken": grant["historyToken"], "category": "conversation",
               "observationSince": NOW.isoformat(), "proactiveSince": NOW.isoformat()}
    caller = "other" if change == "caller" else OWNER
    if change == "character":
        request["characterId"] = "other"
    if change == "snapshot":
        request["snapshotCursor"] = "forged"
    if change == "revoke":
        host.revoke(grant["historyToken"])
    token = HOST_CALLER.set(caller)
    try:
        with pytest.raises(HostServiceError, match="TIMELINE_HISTORY_UNAVAILABLE"):
            host.call("read_turn_page", [request])
    finally:
        HOST_CALLER.reset(token)


def test_history_revocation_releases_unconsumed_large_page_artifact(tmp_path):
    _, artifacts, host, proxy, _, grant = history_for(tmp_path, [
        entry("human", "large"),
        *[entry(f"system-{index}", "large", kind=TimelineKind.SYSTEM, text="x" * 65_536) for index in range(20)],
    ])
    response = proxy.read_turn_page({"characterId": "sakura", "snapshotCursor": grant["snapshotCursor"],
        "historyToken": grant["historyToken"], "category": "conversation",
        "observationSince": NOW.isoformat(), "proactiveSince": NOW.isoformat()})
    assert "artifact" in response and artifacts.count == 1
    host.revoke_scope(OWNER)
    assert artifacts.count == 0


def test_cancellation_after_receiving_a_large_page_releases_its_artifact(tmp_path):
    _, artifacts, _, proxy, _, grant = history_for(tmp_path, [
        entry("human", "large"),
        *[entry(f"system-{index}", "large", kind=TimelineKind.SYSTEM, text="x" * 65_536) for index in range(20)],
    ])
    def cancel_after_response():
        if proxy.responses:
            raise OperationCancelled()
    history = PagedHistory(proxy, "sakura", grant["snapshotCursor"], NOW,
                           ArtifactProxy(artifacts), grant["historyToken"], cancel_after_response)
    with pytest.raises(OperationCancelled):
        select(history, 1_000_000)
    assert artifacts.count == 0
    assert len(proxy.requests) == 1


def test_next_day_model_request_keeps_history_times_and_session_boundaries(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sakura_assistant.agent.runtime import AgentRuntime
    from sakura_assistant.llm.api_client import AssistantModelClient, DialogueSettings
    from tests.model_fixture import LocalModelClient

    yesterday = NOW - timedelta(days=1)
    answered = yesterday + timedelta(minutes=1)
    closed = yesterday + timedelta(minutes=2)
    reopened = NOW - timedelta(minutes=1)
    entries = [
        entry("human", "chat", created_at=yesterday, text="我今天下午要写报告"),
        entry("assistant", "chat", kind=TimelineKind.ASSISTANT, created_at=answered, text="今日の午後ですね。"),
        replace(entry("closed", "closed", kind=TimelineKind.SYSTEM, created_at=closed),
                origin="host", payload={"text": "桌宠正在正常退出，本次运行会话结束。", "eventType": "app.closed"}),
        replace(entry("opened", "opened", kind=TimelineKind.SYSTEM, created_at=reopened),
                origin="host", payload={"text": "桌宠已启动，这是本次运行会话的开始。", "eventType": "app.started"}),
    ]
    store, _, _, _, history, _ = history_for(tmp_path, entries)
    before = store.read_all("sakura")
    client = AssistantModelClient(DialogueSettings(model="model"), model_client=LocalModelClient())
    captured = []

    def post(payload, **_kwargs):
        captured.append(payload)
        return {"choices": [{"message": {"role": "assistant", "content":
            '{"segments":[{"ja":"こんにちは。","zh":"你好。","tone":"中性"}]}'}}]}

    monkeypatch.setattr(client, "_post_chat_completions", post)
    monkeypatch.setattr("sakura_assistant.agent.context_orchestrator.datetime", SimpleNamespace(now=lambda: NOW))
    runtime = AgentRuntime(client, "测试角色", reply_tones=["中性"])
    runtime.context_orchestrator.history = history
    runtime.handle_user_message([{"role": "user", "content": "我回来了"}])

    messages = captured[0]["messages"]
    assert [message["content"] for message in messages if message["role"] == "user"] == [
        "我今天下午要写报告", "我回来了",
    ]
    old_turn = next(index for index, message in enumerate(messages) if message["content"] == "我今天下午要写报告")
    timestamp = messages[old_turn - 1]
    assert timestamp["role"] == "system"
    assert yesterday.isoformat() in timestamp["content"] and answered.isoformat() in timestamp["content"]
    closed_index = next(index for index, message in enumerate(messages) if "本次运行会话结束" in str(message["content"]))
    reopened_index = next(index for index, message in enumerate(messages) if "本次运行会话的开始" in str(message["content"]))
    assert old_turn < closed_index < reopened_index < len(messages) - 1
    assert messages[closed_index]["role"] == messages[reopened_index]["role"] == "system"
    assert closed.isoformat() in messages[closed_index]["content"]
    assert reopened.isoformat() in messages[reopened_index]["content"]
    assert any(NOW.astimezone().isoformat(timespec="seconds") in str(message["content"]) for message in messages)
    assert store.read_all("sakura") == before
    assert {row.entry_id for row in store.read_context_candidates(
        "sakura", observation_since=NOW - timedelta(hours=2), proactive_since=NOW - timedelta(hours=1),
    )} == {item.entry_id for item in entries}
