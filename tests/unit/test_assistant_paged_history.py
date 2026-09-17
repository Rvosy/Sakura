from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from sakura_assistant.llm.token_estimation import estimate_message_tokens


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
OWNER = "sakura.assistant"


def entry(index: str, turn: str, *, kind: TimelineKind = TimelineKind.HUMAN,
          origin: str = "chat", created_at: datetime = NOW, text: str = "hello",
          semantic: bool = False) -> NewTimelineEntry:
    payload = {"text": text}
    if semantic:
        payload["visual"] = {"analysisStatus": "succeeded", "capturedAt": created_at.isoformat(), "imageCount": 1}
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
    cost = estimate_message_tokens({"role": "user", "content": text})
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
    assert len(messages) == 10
    assert {message["role"] for message in messages} == {"user"}
    assert all(message["content"] == text for message in messages)


def test_large_complete_turn_uses_artifact_and_releases_it_after_reading(tmp_path):
    entries = [entry("human", "large")]
    entries.extend(entry(f"system-{index}", "large", kind=TimelineKind.SYSTEM, text="x" * 65_536) for index in range(20))
    _, artifacts, _, proxy, history, _ = history_for(tmp_path, entries)
    snapshot = select(history, 1_000_000)
    assert [turn.turn_id for turn in snapshot.selected_turns] == ["large"]
    messages = history.messages(snapshot)
    assert len(messages) == 21
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


def test_proactive_limit_ttl_and_observation_reply_do_not_duplicate_context(tmp_path):
    entries = [entry("old", "old", kind=TimelineKind.ASSISTANT, origin="proactive", created_at=NOW - timedelta(hours=2))]
    entries.extend(entry(f"proactive-{index}", f"proactive-{index}", kind=TimelineKind.ASSISTANT, origin="proactive", text=f"proactive {index}") for index in range(5))
    entries.extend([
        entry("observation", "observation", kind=TimelineKind.OBSERVATION, origin="scheduled_screen", semantic=True, text="semantic observation"),
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
