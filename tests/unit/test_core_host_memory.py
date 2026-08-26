from __future__ import annotations

import json
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agent import memory as memory_module
from app.agent.memory_curator import MemoryCurationResult, MemoryCurator
from app.agent.memory_recall import MemoryRecallService
from app.llm.prompts.types import ContextRequest
from app.agent.trace import (
    AgentTraceRecorder,
    MessageProvenance,
    PromptTraceMetadata,
)
from app.core_host.runtime_logging import install_runtime_logging
from plugins.builtin.sakura_mem0.boundary import MemoryBoundary, MemoryBoundaryError
from app.storage.timeline import (
    NewTimelineEntry,
    TimelineKind,
    TimelineStore,
)
from app.storage.chat_history import ChatHistoryEntry


class FakeMemoryStore:
    def __init__(self, *, ready: bool = True, model_missing: bool = False) -> None:
        self.ready = ready
        self.model_missing = model_missing
        self.closed = False
        self.preload_calls: list[bool] = []
        self.preload_error = False
        self.block_download = False
        self.download_started = threading.Event()
        self.download_error = False
        self.search_calls: list[dict[str, Any]] = []
        self.memories: dict[str, dict[str, Any]] = {
            "owned": {
                "id": "owned",
                "content": "喜欢樱花与日本语",
                "metadata": {
                    "scope": "sakura",
                    "layer": "semantic",
                    "source": "explicit",
                    "importance": 0.8,
                    "confidence": 0.9,
                },
            }
        }
        self.status_listener = None

    def add_status_listener(self, listener, *, replay: bool = True) -> None:
        self.status_listener = listener
        if replay and self.ready:
            listener("ready", "internal path must not be published")

    def remove_status_listener(self, _listener) -> None:
        self.status_listener = None
        return None

    def become_ready(self) -> None:
        self.ready = True
        if self.status_listener is not None:
            self.status_listener("ready", "private ready detail")

    def is_ready(self) -> bool:
        return self.ready

    def needs_embedding_model_download(self) -> bool:
        return self.model_missing

    def preload(self, *, wait: bool = False) -> None:
        self.preload_calls.append(wait)
        if self.preload_error:
            raise RuntimeError("private preload failure")

    def search_memory(self, arguments, *, wait: bool = False):
        self.search_calls.append(dict(arguments))
        return {"status": "ready", "memories": list(self.memories.values())}

    def create_memory(self, arguments, *, wait: bool = True):
        record = {
            "id": "created",
            "content": arguments["content"],
            "metadata": {**arguments, "scope": "sakura"},
        }
        self.memories["created"] = record
        return {"memory": record}

    def update_memory(self, arguments, *, wait: bool = True):
        record = {
            "id": arguments["id"],
            "content": arguments["content"],
            "metadata": {**arguments, "scope": "sakura"},
        }
        self.memories[str(arguments["id"])] = record
        return {"memory": record}

    def forget_memory(self, arguments, *, wait: bool = True):
        memory_id = str(arguments["id"])
        missing = self.memories.pop(memory_id, None) is None
        return {"already_missing": missing}

    def scoped(self, _scope: str):
        return self

    def download_embedding_model(self, *, progress=None, cancel=None):
        self.download_started.set()
        if progress:
            progress("connecting", 5)
            progress("downloading", 50)
        if self.block_download:
            assert cancel is not None
            cancel.wait(2)
            if cancel.is_set():
                raise memory_module.MemoryModelTaskCancelled("cancelled")
        if self.download_error:
            raise OSError("private cache path must not escape")
        if progress:
            progress("completed", 100)
        return object()

    def import_embedding_model_archive(self, _archive, *, progress=None, cancel=None):
        if progress:
            progress("completed", 100)
        return object()

    def close(self) -> None:
        self.closed = True


class TimelineProxy:
    def __init__(self, store: TimelineStore, character_id: str = "sakura") -> None:
        self.store = store
        self.character_id = character_id

    def read_recent(self, request):
        entries, cursor = self.store.read_recent(self.character_id, request["limit"])
        return {"entries": [_timeline_mapping(entry) for entry in entries], "cursor": cursor}

    def read_since(self, request):
        entries, cursor, has_more = self.store.read_since(
            self.character_id, request["cursor"], request["limit"]
        )
        return {
            "entries": [_timeline_mapping(entry) for entry in entries],
            "nextCursor": cursor,
            "hasMore": has_more,
        }


def _timeline_mapping(entry) -> dict[str, object]:
    return {
        "entryId": entry.entry_id,
        "turnId": entry.turn_id,
        "characterId": entry.character_id,
        "kind": entry.kind.value,
        "origin": entry.origin,
        "createdAt": entry.created_at,
        "payload": entry.payload,
    }


def _timeline(root: Path, turns: int = 1) -> TimelineProxy:
    store = TimelineStore(root / "data" / "chat_history" / "timeline.sqlite3")
    store.initialize()
    entries = []
    for index in range(turns):
        turn_id = f"turn-{index}"
        entries.extend(
            [
                NewTimelineEntry(
                    entry_id=f"human-{index}",
                    turn_id=turn_id,
                    character_id="sakura",
                    kind=TimelineKind.HUMAN,
                    origin="chat",
                    created_at="2026-08-26T12:00:00+08:00",
                    payload={"text": f"请记住樱花 {index}"},
                ),
                NewTimelineEntry(
                    entry_id=f"assistant-{index}",
                    turn_id=turn_id,
                    character_id="sakura",
                    kind=TimelineKind.ASSISTANT,
                    origin="chat",
                    created_at="2026-08-26T12:00:01+08:00",
                    payload={
                        "segments": [
                            {
                                "text": "好的。",
                                "translation": "",
                                "tone": "",
                                "portrait": "",
                                "suppressTts": False,
                            }
                        ]
                    },
                ),
            ]
        )
    store.append_many(entries)
    return TimelineProxy(store)


def _append_timeline_turn(timeline: TimelineProxy, index: int) -> None:
    turn_id = f"turn-{index}"
    timeline.store.append_many(
        [
            NewTimelineEntry(
                entry_id=f"human-{index}",
                turn_id=turn_id,
                character_id="sakura",
                kind=TimelineKind.HUMAN,
                origin="chat",
                created_at="2026-08-26T12:00:00+08:00",
                payload={"text": f"请记住樱花 {index}"},
            ),
            NewTimelineEntry(
                entry_id=f"assistant-{index}",
                turn_id=turn_id,
                character_id="sakura",
                kind=TimelineKind.ASSISTANT,
                origin="chat",
                created_at="2026-08-26T12:00:01+08:00",
                payload={
                    "segments": [
                        {
                            "text": "好的。",
                            "translation": "",
                            "tone": "",
                            "portrait": "",
                            "suppressTts": False,
                        }
                    ]
                },
            ),
        ]
    )


def _root(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "system_config.yaml").write_text(
        yaml.safe_dump(
            {
                "config_version": 4,
                "memory_curation": {
                    "enabled": True,
                    "trigger_turns": 8,
                    "backfill_limit": 200,
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (config / "api.yaml").write_text(
        yaml.safe_dump(
            {
                "api_profiles": [
                    {
                        "id": "fixture",
                        "alias": "Fixture",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "PRIVATE_NOT_PUBLISHED",
                        "models": [{"name": "curator"}],
                    }
                ],
                "model_slots": {},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


def _boundary(
    root: Path,
    store: FakeMemoryStore,
    *,
    recorder: AgentTraceRecorder | None = None,
    config: dict[str, object] | None = None,
) -> MemoryBoundary:
    return MemoryBoundary(
        root,
        "sakura",
        memory_store=store,  # type: ignore[arg-type]
        agent_trace_recorder=recorder,
        curation_config_getter=lambda: dict(config or {}),
    )


def test_memory_search_projects_only_the_frozen_role_scoped_dto(tmp_path: Path) -> None:
    store = FakeMemoryStore()
    boundary = _boundary(_root(tmp_path), store)
    try:
        result = boundary.search({"query": "桜", "limit": 5, "layer": "semantic"})
        assert result["status"] == "ready"
        assert store.search_calls == [{"query": "桜", "limit": 5, "layer": "semantic"}]
        assert result["memories"] == [
            {
                "id": "owned",
                "content": "喜欢樱花与日本语",
                "layer": "semantic",
                "category": "",
                "importance": 0.8,
                "confidence": 0.9,
                "source": "explicit",
                "scope": "sakura",
                "createdAt": "",
                "updatedAt": "",
                "lastAccessedAt": "",
                "score": None,
            }
        ]
    finally:
        boundary.close()
    assert store.closed


def test_missing_embedding_is_empty_degraded_recall_without_implicit_preload(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=True)
    boundary = _boundary(_root(tmp_path), store)
    try:
        result = boundary.search({"query": "chat continues", "limit": 5})
        assert result["status"] == "degraded"
        assert result["memories"] == []
        assert store.search_calls == []
    finally:
        boundary.close()
    assert store.preload_calls == []


def test_installed_embedding_preloads_when_memory_owner_is_created(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=False)
    boundary = _boundary(_root(tmp_path), store)
    try:
        assert store.preload_calls == [False]
        boundary.settings_get()
        boundary.search({"query": "startup", "limit": 5})
        assert store.preload_calls == [False]
    finally:
        boundary.close()


def test_prompt_wait_uses_recalled_memory_after_preload_becomes_ready(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=False)
    boundary = _boundary(_root(tmp_path), store)
    worker = threading.Thread(target=lambda: (time.sleep(0.05), store.become_ready()))
    worker.start()
    try:
        snapshot = boundary.wait_until_settled(1.0)
        result = boundary.search_memory({"query": "桜", "limit": 5})
        assert snapshot == {"status": "ready", "message": ""}
        assert result["status"] == "ready"
        assert len(result["memories"]) == 1
    finally:
        worker.join(1)
        boundary.close()


def test_prompt_wait_times_out_and_honors_cancellation(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=False)
    boundary = _boundary(_root(tmp_path), store)
    calls = 0

    def cancel_checker() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled-for-test")

    try:
        started = time.monotonic()
        assert boundary.wait_until_settled(0.02)["status"] == "loading"
        assert time.monotonic() - started < 0.5
        with pytest.raises(RuntimeError, match="cancelled-for-test"):
            boundary.wait_until_settled(1.0, cancel_checker=cancel_checker)
    finally:
        boundary.close()


def test_startup_preload_failure_is_degraded_without_escaping_private_error(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=False)
    store.preload_error = True
    boundary = _boundary(_root(tmp_path), store)
    try:
        assert store.preload_calls == [False]
        assert boundary.status() == {
            "status": "degraded",
            "message": "记忆暂时不可用；聊天不受影响。",
        }
        assert "private" not in str(boundary.settings_get())
    finally:
        boundary.close()


def test_plugin_owner_diagnostic_omits_query_content_secrets_and_paths(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = root / "data" / "logs" / memory_module.MEMORY_INITIALIZATION_LOG_NAME
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    store = FakeMemoryStore(ready=False, model_missing=False)
    store.preload_error = True
    boundary = _boundary(root, store)
    try:
        boundary.settings_get()
        boundary.search({"query": "PRIVATE_QUERY C:\\Users\\owner\\memory", "limit": 5})
    finally:
        boundary.close()

    text = path.read_text(encoding="utf-8")
    assert "PRIVATE_QUERY" not in text
    assert "PRIVATE_NOT_PUBLISHED" not in text
    assert "private preload failure" not in text
    assert str(root) not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert events
    assert {event.get("component") for event in events} == {"plugin_memory_owner"}
    allowed_fields = {
        "timestampMs",
        "component",
        "event",
        "pid",
        "stage",
        "outcome",
        "status",
        "category",
        "errorType",
        "elapsedMs",
        "wait",
        "modelCached",
        "childPid",
        "processAlive",
        "request",
    }
    assert all(set(event) <= allowed_fields for event in events)


def test_crud_is_bounded_and_delete_is_idempotent(tmp_path: Path) -> None:
    boundary = _boundary(_root(tmp_path), FakeMemoryStore())
    try:
        created = boundary.upsert(
            {
                "content": "中文入力と日本語入力",
                "layer": "semantic",
                "category": "preference",
                "source": "explicit",
                "importance": 0.7,
                "confidence": 0.9,
            }
        )
        assert created["memory"]["scope"] == "sakura"  # type: ignore[index]
        assert boundary.delete({"id": "created"})["alreadyMissing"] is False
        assert boundary.delete({"id": "created"})["alreadyMissing"] is True
        with pytest.raises(MemoryBoundaryError, match="未知字段"):
            boundary.search({"query": "x", "limit": 5, "scope": "other"})
    finally:
        boundary.close()


def test_upsert_rejects_authoritative_metadata_mismatch(tmp_path: Path) -> None:
    class ConflictingStore(FakeMemoryStore):
        def create_memory(self, arguments, *, wait=True):
            return {
                "memory": {
                    "id": "created",
                    "content": arguments["content"],
                    "metadata": {
                        **arguments,
                        "layer": "semantic",
                        "scope": "sakura",
                    },
                }
            }

    boundary = _boundary(_root(tmp_path), ConflictingStore())
    try:
        with pytest.raises(MemoryBoundaryError) as error:
            boundary.upsert(
                {
                    "content": "周末和同事聚餐",
                    "layer": "episodic",
                    "category": "schedule",
                    "importance": 0.6,
                    "confidence": 0.9,
                }
            )
        assert error.value.code == "MEMORY_ROUND_TRIP_MISMATCH"
    finally:
        boundary.close()


def test_recall_filters_memory_created_in_current_turn() -> None:
    class Memory:
        def search_memory(self, arguments, *, wait=False):
            return {
                "status": "ready",
                "memories": [
                    {
                        "id": "same-turn",
                        "content": "周末和同事聚餐",
                        "score": 0.95,
                        "metadata": {"created_in_turn_id": "turn-now"},
                    },
                    {
                        "id": "older",
                        "content": "用户喜欢樱花",
                        "score": 0.9,
                        "metadata": {"created_in_turn_id": "turn-before"},
                    },
                ],
            }

    result = MemoryRecallService(Memory()).recall(
        ContextRequest(current_input="周末安排", current_turn_id="turn-now")
    )
    assert [fragment.metadata["memory_id"] for fragment in result.fragments] == ["older"]


def test_memory_curation_requests_at_most_one_provider_repair_for_invalid_json() -> None:
    class Api:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def complete_raw(self, *_args, **kwargs):
            self.calls.append(kwargs["trace_metadata"].purpose)
            return "{invalid" if len(self.calls) == 1 else '{"operations":[]}'

    class Store:
        def list_memories(self, *, limit=None):
            return []

    api = Api()
    result = MemoryCurator(api, Store()).curate_entries(
        [
            ChatHistoryEntry(
                created_at="2026-08-26T12:00:00+08:00",
                role="observation",
                content="画面摘要：用户正在修复测试失败。",
                entry_id="observation-1",
                turn_id="turn-1",
                origin="scheduled_screen",
                evidence_ready=True,
            )
        ]
    )
    assert result.returned == 0
    assert api.calls == ["memory_curation", "memory_curation_repair"]


def test_plugin_config_is_independent_from_core_curation_documents(tmp_path: Path) -> None:
    root = _root(tmp_path)
    api_path = root / "config" / "api.yaml"
    system_path = root / "config" / "system_config.yaml"
    api_before = api_path.read_bytes()
    system_before = system_path.read_bytes()
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 12,
            "backfillLimit": 321,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    try:
        snapshot = boundary.settings_get()
        assert snapshot["curation"]["triggerTurns"] == 12  # type: ignore[index]
        assert snapshot["curation"]["backfillLimit"] == 321  # type: ignore[index]
        assert snapshot["curationModelSlot"] == {"profileId": "fixture", "model": "curator"}
        assert "PRIVATE_NOT_PUBLISHED" not in str(snapshot)
    finally:
        boundary.close()
    assert api_path.read_bytes() == api_before
    assert system_path.read_bytes() == system_before


def test_plugin_defaults_do_not_import_old_core_curation_fields(tmp_path: Path) -> None:
    root = _root(tmp_path)
    system_path = root / "config" / "system_config.yaml"
    system = yaml.safe_load(system_path.read_text(encoding="utf-8"))
    system["memory_curation"] = {"trigger_turns": 17, "backfill_limit": 777}
    system_path.write_text(yaml.safe_dump(system, sort_keys=False), encoding="utf-8")
    api_path = root / "config" / "api.yaml"
    api = yaml.safe_load(api_path.read_text(encoding="utf-8"))
    api["model_slots"]["memory_curation"] = {
        "profile_id": "fixture",
        "model": "curator",
    }
    api_path.write_text(yaml.safe_dump(api, sort_keys=False), encoding="utf-8")
    boundary = _boundary(root, FakeMemoryStore(), config={})
    try:
        snapshot = boundary.settings_get()
        assert snapshot["curation"]["triggerTurns"] == 8  # type: ignore[index]
        assert snapshot["curation"]["backfillLimit"] == 200  # type: ignore[index]
        assert snapshot["curationModelSlot"] == {"profileId": "", "model": ""}
    finally:
        boundary.close()


def test_empty_plugin_curation_slot_inherits_chat_model(tmp_path: Path) -> None:
    root = _root(tmp_path)
    api_path = root / "config" / "api.yaml"
    api = yaml.safe_load(api_path.read_text(encoding="utf-8"))
    api["model_slots"]["chat"] = {
        "profile_id": "fixture",
        "model": "curator",
    }
    api_path.write_text(
        yaml.safe_dump(api, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "curationProfileId": "",
            "curationModel": "",
        },
    )
    try:
        snapshot = boundary.settings_get()
        assert snapshot["curationModelSlot"] == {"profileId": "", "model": ""}
        assert snapshot["curation"]["available"] is True  # type: ignore[index]
    finally:
        boundary.close()


def test_completed_turn_curation_commits_cursor_only_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 1,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    timeline = _timeline(root)
    calls: list[int] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            self.agent_trace_recorder = agent_trace_recorder

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            if cancel_checker:
                cancel_checker()
            calls.append(len(entries))
            from app.agent.memory_curator import MemoryCurationResult

            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        state = boundary._curation_state.snapshot()  # noqa: SLF001 - domain cursor contract
        while not state["timeline_cursor"] and time.monotonic() < deadline:
            time.sleep(0.01)
            state = boundary._curation_state.snapshot()  # noqa: SLF001
        assert calls == [2]
        assert state["timeline_cursor"] == timeline.store.latest_cursor("sakura")
        assert state["pending_turns"] == 0

        boundary.note_timeline_changed(timeline)
        time.sleep(0.05)
        assert calls == [2]
    finally:
        boundary.close()


def test_scheduled_observation_counts_only_after_semantic_analysis_and_once_per_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    timeline = _timeline(root, turns=0)
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 1,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            calls.append([entry.entry_id for entry in entries])
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        timeline.store.append_many(
            [
                NewTimelineEntry(
                    entry_id="capture-only",
                    turn_id="capture-turn",
                    character_id="sakura",
                    kind=TimelineKind.OBSERVATION,
                    origin="scheduled_screen",
                    created_at="2026-08-26T12:00:00+08:00",
                    payload={"text": "截图已提交", "visual": {"imageCount": 1}},
                ),
                NewTimelineEntry(
                    entry_id="capture-assistant",
                    turn_id="capture-turn",
                    character_id="sakura",
                    kind=TimelineKind.ASSISTANT,
                    origin="proactive",
                    created_at="2026-08-26T12:00:01+08:00",
                    payload={
                        "segments": [
                            {
                                "text": "先看看。",
                                "translation": "",
                                "tone": "",
                                "portrait": "",
                                "suppressTts": False,
                            }
                        ]
                    },
                ),
            ]
        )
        boundary.note_timeline_changed(timeline)
        assert calls == []
        assert boundary._curation_state.pending_turns() == 0  # noqa: SLF001

        timeline.store.append_many(
            [
                NewTimelineEntry(
                    entry_id="semantic-observation",
                    turn_id="semantic-turn",
                    character_id="sakura",
                    kind=TimelineKind.OBSERVATION,
                    origin="scheduled_screen",
                    created_at="2026-08-26T12:04:00+08:00",
                    payload={
                        "text": "画面摘要：用户正在修复测试失败。",
                        "visual": {
                            "imageCount": 1,
                            "analysisStatus": "succeeded",
                            "confidence": 0.9,
                            "sensitiveRedacted": False,
                        },
                    },
                ),
                NewTimelineEntry(
                    entry_id="semantic-assistant",
                    turn_id="semantic-turn",
                    character_id="sakura",
                    kind=TimelineKind.ASSISTANT,
                    origin="proactive",
                    created_at="2026-08-26T12:04:01+08:00",
                    payload={
                        "segments": [
                            {
                                "text": "这个失败点值得记一下。",
                                "translation": "",
                                "tone": "",
                                "portrait": "",
                                "suppressTts": False,
                            }
                        ]
                    },
                ),
            ]
        )
        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == [["semantic-observation", "semantic-assistant"]]
    finally:
        boundary.close()


def test_next_completion_event_catches_up_a_missed_timeline_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    timeline = _timeline(root, turns=2)
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 2,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            calls.append([entry.entry_id for entry in entries])
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        # Only the second completion notification arrives; the read is from the
        # saved cursor, not from the event body, so both committed turns appear.
        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while not boundary._curation_state.timeline_cursor() and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert calls == [["human-0", "assistant-0", "human-1", "assistant-1"]]
    finally:
        boundary.close()


def test_completion_arriving_during_curation_runs_one_followup_catchup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    timeline = _timeline(root)
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 1,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            calls.append([entry.entry_id for entry in entries])
            if len(calls) == 1:
                first_started.set()
                assert release_first.wait(2)
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_timeline_changed(timeline)
        assert first_started.wait(1)
        _append_timeline_turn(timeline, 1)
        boundary.note_timeline_changed(timeline)
        release_first.set()

        final_cursor = timeline.store.latest_cursor("sakura")
        deadline = time.monotonic() + 2
        while boundary._curation_state.timeline_cursor() != final_cursor and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert calls == [
            ["human-0", "assistant-0"],
            ["human-1", "assistant-1"],
        ]
        assert boundary._curation_state.timeline_cursor() == final_cursor  # noqa: SLF001
    finally:
        release_first.set()
        boundary.close()


def test_plugin_restart_catches_up_from_saved_timeline_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    timeline = _timeline(root)
    config = {
        "triggerTurns": 2,
        "curationProfileId": "fixture",
        "curationModel": "curator",
    }
    first = _boundary(root, FakeMemoryStore(), config=config)
    first.note_timeline_changed(timeline)
    assert first._curation_state.timeline_cursor() == ""  # noqa: SLF001
    assert first._curation_state.pending_turns() == 1  # noqa: SLF001
    first.close()
    _append_timeline_turn(timeline, 1)
    calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            calls.append([entry.entry_id for entry in entries])
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    restarted = _boundary(root, FakeMemoryStore(), config=config)
    try:
        restarted.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while not restarted._curation_state.timeline_cursor() and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert calls == [["human-0", "assistant-0", "human-1", "assistant-1"]]
        assert restarted._curation_state.timeline_cursor() == timeline.store.latest_cursor("sakura")  # noqa: SLF001
    finally:
        restarted.close()


def test_failed_curation_keeps_cursor_and_retry_does_not_duplicate_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    timeline = _timeline(root)
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 1,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    calls = 0

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("fixture failure")
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while boundary._curation_active and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert boundary._curation_state.timeline_cursor() == ""  # noqa: SLF001

        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while not boundary._curation_state.timeline_cursor() and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        boundary.note_timeline_changed(timeline)
        time.sleep(0.05)
        assert calls == 2
        assert boundary._curation_state.timeline_cursor() == timeline.store.latest_cursor("sakura")  # noqa: SLF001
    finally:
        boundary.close()


def test_invalid_saved_cursor_uses_configured_recent_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    timeline = _timeline(root)
    foreign = TimelineStore(root / "data" / "chat_history" / "replacement.sqlite3")
    foreign.initialize()
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        config={
            "triggerTurns": 1,
            "backfillLimit": 2,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    boundary._curation_state.mark_timeline_processed(foreign.latest_cursor("sakura"))  # noqa: SLF001
    calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            calls.append([entry.entry_id for entry in entries])
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while boundary._curation_state.timeline_cursor() != timeline.store.latest_cursor("sakura") and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert calls == [["human-0", "assistant-0"]]
    finally:
        boundary.close()


def test_timeline_cursor_state_survives_a_b_a_role_switch_beyond_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    store = TimelineStore(root / "data" / "chat_history" / "timeline.sqlite3")
    store.initialize()

    def append_turn(character_id: str, index: int) -> None:
        turn_id = f"{character_id}-turn-{index}"
        store.append_many(
            [
                NewTimelineEntry(
                    entry_id=f"{character_id}-human-{index}",
                    turn_id=turn_id,
                    character_id=character_id,
                    kind=TimelineKind.HUMAN,
                    origin="chat",
                    created_at="2026-08-26T12:00:00+08:00",
                    payload={"text": f"remember {character_id} {index}"},
                ),
                NewTimelineEntry(
                    entry_id=f"{character_id}-assistant-{index}",
                    turn_id=turn_id,
                    character_id=character_id,
                    kind=TimelineKind.ASSISTANT,
                    origin="chat",
                    created_at="2026-08-26T12:00:01+08:00",
                    payload={
                        "segments": [
                            {
                                "text": "ok",
                                "translation": "",
                                "tone": "",
                                "portrait": "",
                                "suppressTts": False,
                            }
                        ]
                    },
                ),
            ]
        )

    append_turn("alice", 0)
    append_turn("bob", 0)
    calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeCurator:
        def __init__(self, _client, _store, *, system_prompt: str = "") -> None:
            pass

        def curate_entries(self, entries, *, cancel_checker=None):
            calls.append([entry.entry_id for entry in entries])
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    config = {
        "triggerTurns": 1,
        "backfillLimit": 2,
        "curationProfileId": "fixture",
        "curationModel": "curator",
    }

    def consume(character_id: str) -> None:
        boundary = MemoryBoundary(
            root,
            character_id,
            memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
            curation_config_getter=lambda: config,
        )
        timeline = TimelineProxy(store, character_id)
        try:
            boundary.note_timeline_changed(timeline)
            expected = store.latest_cursor(character_id)
            deadline = time.monotonic() + 2
            while boundary._curation_state.timeline_cursor() != expected and time.monotonic() < deadline:  # noqa: SLF001
                time.sleep(0.01)
            assert boundary._curation_state.timeline_cursor() == expected  # noqa: SLF001
        finally:
            boundary.close()

    consume("alice")
    consume("bob")
    for index in range(1, 4):
        append_turn("alice", index)
    consume("alice")

    assert calls == [
        ["alice-human-0", "alice-assistant-0"],
        ["bob-human-0", "bob-assistant-0"],
        [
            "alice-human-1",
            "alice-assistant-1",
            "alice-human-2",
            "alice-assistant-2",
            "alice-human-3",
            "alice-assistant-3",
        ],
    ]


def test_newly_curated_memory_keeps_source_timeline_entry_ids() -> None:
    writes: list[dict[str, object]] = []

    class Api:
        def complete_raw(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "operations": [
                        {
                            "op": "add",
                            "content": "用户喜欢樱花",
                            "layer": "semantic",
                            "confidence": 0.9,
                            "importance": 0.8,
                        }
                    ]
                },
                ensure_ascii=False,
            )

    class Store:
        def list_memories(self, *, limit=None):
            return []

        def create_memory(self, arguments, *, allow_sensitive=False):
            writes.append(dict(arguments))
            return {"memory": {"id": "created", "content": arguments["content"]}}

    entries = [
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:00+08:00",
            role="user",
            content="我喜欢樱花",
            entry_id="timeline-human",
        ),
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:01+08:00",
            role="assistant",
            content="记住了",
            entry_id="timeline-assistant",
        ),
    ]

    result = MemoryCurator(Api(), Store()).curate_entries(entries)

    assert result.created == 1
    assert writes[0]["source_entry_ids"] == ["timeline-human", "timeline-assistant"]
    metadata = memory_module._memory_metadata(  # noqa: SLF001 - metadata contract
        writes[0],
        scope_id="sakura",
        created_at="2026-08-26T12:00:02+08:00",
        updated_at="2026-08-26T12:00:02+08:00",
    )
    assert metadata["source_entry_ids"] == ["timeline-human", "timeline-assistant"]


def test_partial_backend_success_is_idempotent_when_same_entries_are_retried() -> None:
    class Api:
        def __init__(self) -> None:
            self.calls = 0

        def complete_raw(self, *_args, **_kwargs):
            self.calls += 1
            operations = [
                {
                    "op": "add",
                    "content": "用户喜欢樱花",
                    "layer": "semantic",
                    "confidence": 0.9,
                },
                {
                    "op": "add",
                    "content": "用户在学习日语",
                    "layer": "semantic",
                    "confidence": 0.9,
                },
            ]
            if self.calls > 1:
                operations = operations[1:]
            return json.dumps(
                {"operations": operations},
                ensure_ascii=False,
            )

    class Store:
        def __init__(self) -> None:
            self.calls = 0
            self.records: list[dict[str, object]] = []

        def list_memories(self, *, limit=None):
            return list(self.records)

        def create_memory(self, arguments, *, allow_sensitive=False):
            self.calls += 1
            if self.calls == 2:
                raise OSError("second write failed")
            metadata = memory_module._memory_metadata(  # noqa: SLF001
                dict(arguments),
                scope_id="sakura",
                created_at="2026-08-26T12:00:02+08:00",
                updated_at="2026-08-26T12:00:02+08:00",
            )
            record = {
                "id": f"memory-{len(self.records)}",
                "content": arguments["content"],
                "metadata": metadata,
            }
            self.records.append(record)
            return {"memory": record}

    entries = [
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:00+08:00",
            role="user",
            content="我喜欢樱花，也在学习日语",
            entry_id="timeline-human",
        ),
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:01+08:00",
            role="assistant",
            content="记住了",
            entry_id="timeline-assistant",
        ),
    ]
    api = Api()
    store = Store()
    curator = MemoryCurator(api, store)

    with pytest.raises(RuntimeError, match="MEMORY_CURATION_WRITE_FAILED"):
        curator.curate_entries(entries)
    assert [record["content"] for record in store.records] == ["用户喜欢樱花"]

    result = curator.curate_entries(entries)

    assert result.created == 1
    assert result.ignored == 0
    assert [record["content"] for record in store.records] == [
        "用户喜欢樱花",
        "用户在学习日语",
    ]


def test_partial_backend_retry_can_reorder_operations_without_duplicate_add() -> None:
    source_ids = ["timeline-human", "timeline-assistant"]
    created: list[dict[str, object]] = []

    class Api:
        def complete_raw(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "operations": [
                        {
                            "op": "add",
                            "content": "用户在学习韩语",
                            "layer": "semantic",
                            "confidence": 0.9,
                        },
                        {
                            "op": "add",
                            "content": "用户正在学习日语",
                            "layer": "semantic",
                            "confidence": 0.9,
                        },
                    ]
                },
                ensure_ascii=False,
            )

    class Store:
        def list_memories(self, *, limit=None):
            return [
                {
                    "id": "already-written",
                    "content": "用户在学习日语",
                    "metadata": {
                        "layer": "semantic",
                        "source_entry_ids": source_ids,
                    },
                }
            ]

        def create_memory(self, arguments, *, allow_sensitive=False):
            created.append(dict(arguments))
            return {"memory": {"id": "new", "content": arguments["content"]}}

    entries = [
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:00+08:00",
            role="user",
            content="我喜欢樱花，也在学习日语",
            entry_id=source_ids[0],
        ),
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:01+08:00",
            role="assistant",
            content="记住了",
            entry_id=source_ids[1],
        ),
    ]

    result = MemoryCurator(Api(), Store()).curate_entries(entries)

    assert result.created == 1
    assert result.ignored == 1
    assert [item["content"] for item in created] == ["用户在学习韩语"]


def test_existing_memory_snapshot_failure_aborts_curation_before_write() -> None:
    api_calls = 0
    writes = 0

    class Api:
        def complete_raw(self, *_args, **_kwargs):
            nonlocal api_calls
            api_calls += 1
            return '{"operations":[]}'

    class Store:
        def list_memories(self, *, limit=None):
            raise OSError("snapshot unavailable")

        def create_memory(self, arguments, *, allow_sensitive=False):
            nonlocal writes
            writes += 1

    entries = [
        ChatHistoryEntry(
            created_at="2026-08-26T12:00:00+08:00",
            role="user",
            content="请记住",
            entry_id="timeline-human",
        )
    ]

    with pytest.raises(RuntimeError, match="MEMORY_CURATION_SNAPSHOT_FAILED"):
        MemoryCurator(Api(), Store()).curate_entries(entries)
    assert api_calls == 0
    assert writes == 0


def test_backend_write_failure_does_not_advance_timeline_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStore(FakeMemoryStore):
        def list_memories(self, *, limit=None):
            return []

        def create_memory(self, arguments, *, allow_sensitive=False, wait=True):
            raise OSError("backend unavailable")

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            pass

        def complete_raw(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "operations": [
                        {
                            "op": "add",
                            "content": "用户喜欢樱花",
                            "layer": "semantic",
                            "confidence": 0.9,
                        }
                    ]
                },
                ensure_ascii=False,
            )

        def close(self) -> None:
            pass

    root = _root(tmp_path)
    timeline = _timeline(root)
    boundary = _boundary(
        root,
        FailingStore(),
        config={
            "triggerTurns": 1,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    try:
        boundary.note_timeline_changed(timeline)
        deadline = time.monotonic() + 2
        while boundary._curation_active and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert boundary._curation_state.timeline_cursor() == ""  # noqa: SLF001
        assert boundary._curation_state.pending_turns() == 1  # noqa: SLF001
    finally:
        boundary.close()


def test_background_curation_has_independent_operation_runtime_correlation_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    recorder = AgentTraceRecorder(root)
    boundary = _boundary(
        root,
        FakeMemoryStore(),
        recorder=recorder,
        config={
            "triggerTurns": 1,
            "curationProfileId": "fixture",
            "curationModel": "curator",
        },
    )
    timeline = _timeline(root)
    runtime_stream = __import__("io").BytesIO()
    bridge = install_runtime_logging(runtime_stream)

    class FakeClient:
        def __init__(self, _settings, *, agent_trace_recorder=None) -> None:
            self.recorder = agent_trace_recorder

        def close(self) -> None:
            return None

    class FakeCurator:
        def __init__(self, client, _store, *, system_prompt: str = "") -> None:
            self.client = client

        def curate_entries(self, entries, *, cancel_checker=None):
            if cancel_checker:
                cancel_checker()
            call = self.client.recorder.start_model_call(
                model="curator",
                payload={
                    "model": "curator",
                    "messages": [
                        {"role": "system", "content": "fixed persona"},
                        {"role": "user", "content": "需要整理的真实对话"},
                    ],
                },
                prompt_provenance=[
                    MessageProvenance("system_prompt"),
                    MessageProvenance("user_input"),
                ],
                metadata=PromptTraceMetadata(purpose="memory_curation"),
            )
            self.client.recorder.record_model_reply(
                call,
                raw_message={"role": "assistant", "content": '{"operations":[]}'},
                usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            )
            return MemoryCurationResult(processed_entries=len(entries))

    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("plugins.builtin.sakura_mem0.boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_timeline_changed(timeline)
        trace_path = root / "data" / "logs" / "sakura-agent-trace.log"
        deadline = time.monotonic() + 2
        while not trace_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert trace_path.exists()
        trace = trace_path.read_text(encoding="utf-8")
        assert "用途" in trace and "记忆整理" in trace
        assert "需要整理的真实对话" in trace
        assert "模型请求" in trace and "模型回复" in trace
    finally:
        boundary.close()
        bridge.close()

    records = [
        json.loads(line.removeprefix(b"SAKURA_RUNTIME_LOG_V1\t"))
        for line in runtime_stream.getvalue().splitlines()
    ]
    curation = [record for record in records if str(record.get("event", "")).startswith("memory.curation.")]
    assert [record["event"] for record in curation] == [
        "memory.curation.started",
        "memory.curation.finished",
    ]
    assert all(str(record.get("operation_id", "")).startswith("memory-curation-") for record in curation)


def test_model_download_reservation_closes_cancel_race_and_preserves_task_identity(
    tmp_path: Path,
) -> None:
    store = FakeMemoryStore(ready=False, model_missing=True)
    store.block_download = True
    boundary = _boundary(_root(tmp_path), store)
    result: list[str] = []
    boundary.begin_model_download("memory-model-task")

    worker = threading.Thread(
        target=lambda: result.append(boundary.run_model_download("memory-model-task")),
        daemon=True,
    )
    worker.start()
    assert store.download_started.wait(2)
    cancelled = boundary.model_cancel({"taskHandle": "memory-model-task"})
    worker.join(2)

    try:
        assert cancelled == {"accepted": True, "taskId": "memory-model-task"}
        assert result == ["cancelled"]
        assert boundary.model_cancel({"taskHandle": "memory-model-task"}) == {
            "accepted": False,
            "taskId": "",
        }
    finally:
        boundary.close()


def test_model_download_failure_is_sanitized_and_releases_task(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=True)
    store.download_error = True
    boundary = _boundary(_root(tmp_path), store)
    try:
        boundary.begin_model_download("memory-model-failure")
        assert boundary.run_model_download("memory-model-failure") == "failed"
        assert boundary.status() == {
            "status": "degraded",
            "message": "本地记忆模型下载失败；原缓存保持不变。",
        }
        assert boundary.model_cancel({"taskHandle": "memory-model-failure"})["accepted"] is False
    finally:
        boundary.close()


def test_model_download_failure_keeps_previous_cache_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = (
        tmp_path
        / "runtime"
        / "fastembed-cache"
        / memory_module.DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    )
    cache.mkdir(parents=True)
    (cache / "old.bin").write_bytes(b"previous-readable-cache")

    def fail_download(_repo_id, staging_root, **_kwargs):
        partial = Path(staging_root) / memory_module.DEFAULT_EMBEDDING_MODEL_CACHE_NAME
        partial.mkdir(parents=True)
        (partial / "partial.bin").write_bytes(b"partial")
        raise OSError("network interrupted")

    monkeypatch.setattr(memory_module, "_download_hf_snapshot", fail_download)
    with pytest.raises(memory_module.MemoryModelImportError):
        memory_module.download_embedding_model(tmp_path)

    assert (cache / "old.bin").read_bytes() == b"previous-readable-cache"
    assert not list(cache.parent.glob(".memory_model_download_*"))


def test_model_import_rejects_bad_onnx_artifacts_and_keeps_previous_cache(
    tmp_path: Path,
) -> None:
    cache = (
        tmp_path
        / "runtime"
        / "fastembed-cache"
        / memory_module.DEFAULT_EMBEDDING_MODEL_CACHE_NAME
    )
    cache.mkdir(parents=True)
    (cache / "old.bin").write_bytes(b"previous-readable-cache")
    archive = tmp_path / "bad-onnx.zip"
    prefix = (
        f"{memory_module.DEFAULT_EMBEDDING_MODEL_CACHE_NAME}/snapshots/"
        f"{memory_module.DEFAULT_EMBEDDING_ARTIFACT_REVISION}"
    )
    with zipfile.ZipFile(archive, "w") as zf:
        for filename in memory_module.DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES:
            zf.writestr(f"{prefix}/{filename}", b"not-the-pinned-artifact")

    with pytest.raises(memory_module.MemoryModelImportError, match="文件大小不匹配"):
        memory_module.import_embedding_model_archive(archive, tmp_path)

    assert (cache / "old.bin").read_bytes() == b"previous-readable-cache"
    assert not list(cache.parent.glob(".memory_model_import_*"))
