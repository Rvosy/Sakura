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
from app.agent.memory_curator import MemoryCurationResult
from app.agent.trace import (
    AgentTraceRecorder,
    AgentTraceSettings,
    MessageProvenance,
    PromptTraceMetadata,
)
from app.core_host.runtime_logging import install_runtime_logging
from app.core_host.memory_boundary import MemoryBoundary, MemoryBoundaryError
from app.storage.chat_history import ChatHistoryStore
from app.llm.api_client import ApiSettings


class FakeMemoryStore:
    def __init__(self, *, ready: bool = True, model_missing: bool = False) -> None:
        self.ready = ready
        self.model_missing = model_missing
        self.closed = False
        self.preload_calls: list[bool] = []
        self.preload_error = False
        self.block_download = False
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


def _root(tmp_path: Path) -> Path:
    config = tmp_path / "data" / "config"
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
    (tmp_path / "data" / "memory.json").write_bytes(b"legacy-memory-bytes\x00\xff")
    return tmp_path


def _boundary(
    root: Path,
    store: FakeMemoryStore,
    *,
    recorder: AgentTraceRecorder | None = None,
) -> MemoryBoundary:
    return MemoryBoundary(
        root,
        "sakura",
        ApiSettings(base_url="https://example.invalid/v1", api_key="private", model="chat"),
        memory_store=store,  # type: ignore[arg-type]
        agent_trace_recorder=recorder,
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


def test_memory_diagnostic_timeline_omits_query_content_secrets_and_paths(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = root / "data" / "logs" / memory_module.MEMORY_INITIALIZATION_LOG_NAME
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    store = FakeMemoryStore(ready=False, model_missing=False)
    store.preload_error = True
    boundary = _boundary(root, store)
    try:
        boundary.handle("memory.settings.get", {})
        boundary.handle(
            "memory.search",
            {"query": "PRIVATE_QUERY C:\\Users\\owner\\memory", "limit": 5},
        )
    finally:
        boundary.close()

    text = path.read_text(encoding="utf-8")
    assert "PRIVATE_QUERY" not in text
    assert "PRIVATE_NOT_PUBLISHED" not in text
    assert "private preload failure" not in text
    assert str(root) not in text
    events = [json.loads(line) for line in text.splitlines()]
    requests = {event.get("request") for event in events}
    assert {"memory.settings.get", "memory.search"} <= requests
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


def test_settings_save_preserves_backfill_other_slots_and_legacy_memory_bytes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    legacy = (root / "data" / "memory.json").read_bytes()
    boundary = _boundary(root, FakeMemoryStore())
    try:
        result = boundary.settings_save(
            {
                "triggerTurns": 12,
                "curationModelSlot": {"profileId": "fixture", "model": "curator"},
            }
        )
        assert result["changePlan"] == "core_restart_required"
        snapshot = boundary.settings_get()
        assert snapshot["curation"]["triggerTurns"] == 12  # type: ignore[index]
        assert snapshot["curation"]["backfillLimit"] == 200  # type: ignore[index]
        assert snapshot["curationModelSlot"] == {"profileId": "fixture", "model": "curator"}
        assert "PRIVATE_NOT_PUBLISHED" not in str(snapshot)
    finally:
        boundary.close()
    assert (root / "data" / "memory.json").read_bytes() == legacy


def test_completed_turn_curation_commits_cursor_only_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    boundary = _boundary(root, FakeMemoryStore())
    boundary.settings_save(
        {
            "triggerTurns": 1,
            "curationModelSlot": {"profileId": "fixture", "model": "curator"},
        }
    )
    history = ChatHistoryStore(root / "data" / "chat_history" / "sakura.jsonl")
    history.append("user", "请记住我喜欢桜")
    history.append("assistant", "好的。")
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

    monkeypatch.setattr("app.core_host.memory_boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("app.core_host.memory_boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_completed_chat(history)
        deadline = time.monotonic() + 2
        state = boundary._curation_state.snapshot()  # noqa: SLF001 - domain cursor contract
        while state["processed_history_count"] != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
            state = boundary._curation_state.snapshot()  # noqa: SLF001
        assert calls == [2]
        assert state["processed_history_count"] == 2
        assert state["pending_turns"] == 0
    finally:
        boundary.close()


def test_background_curation_has_independent_operation_runtime_correlation_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    recorder = AgentTraceRecorder(root, AgentTraceSettings(enabled=True))
    boundary = _boundary(root, FakeMemoryStore(), recorder=recorder)
    boundary.settings_save(
        {
            "triggerTurns": 1,
            "curationModelSlot": {"profileId": "fixture", "model": "curator"},
        }
    )
    history = ChatHistoryStore(root / "data" / "chat_history" / "sakura.jsonl")
    history.append("user", "请记住我喜欢桜")
    history.append("assistant", "好的。")
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

    monkeypatch.setattr("app.core_host.memory_boundary.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("app.core_host.memory_boundary.MemoryCurator", FakeCurator)
    try:
        boundary.note_completed_chat(history)
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


def test_model_download_cancel_emits_one_terminal_and_preserves_task_identity(
    tmp_path: Path,
) -> None:
    store = FakeMemoryStore(ready=False, model_missing=True)
    store.block_download = True
    boundary = _boundary(_root(tmp_path), store)
    events: list[dict[str, Any]] = []
    boundary.set_event_publisher(events.append)
    request = {
        "protocolMinor": 2,
        "generationCredential": "credential",
        "id": "memory-model-task",
        "name": "memory.model.download",
    }
    result: dict[str, object] = {}

    worker = threading.Thread(
        target=lambda: result.update(boundary.model_download(request)),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 2
    while not any(event["name"] == "memory.model.started" for event in events):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    cancelled = boundary.model_cancel({"taskHandle": "memory-model-task"})
    worker.join(2)

    try:
        assert cancelled == {"accepted": True, "taskId": "memory-model-task"}
        assert result["status"] == "cancelled"
        terminals = [
            event
            for event in events
            if event["name"]
            in {"memory.model.completed", "memory.model.failed", "memory.model.cancelled"}
        ]
        assert [event["name"] for event in terminals] == ["memory.model.cancelled"]
        assert all(event["payload"]["taskId"] == "memory-model-task" for event in events)
    finally:
        boundary.close()


def test_model_download_failure_is_sanitized_and_has_one_terminal(tmp_path: Path) -> None:
    store = FakeMemoryStore(ready=False, model_missing=True)
    store.download_error = True
    boundary = _boundary(_root(tmp_path), store)
    events: list[dict[str, Any]] = []
    boundary.set_event_publisher(events.append)
    request = {
        "protocolMinor": 2,
        "generationCredential": "credential",
        "id": "memory-model-failure",
        "name": "memory.model.download",
    }
    try:
        result = boundary.model_download(request)
        terminal = [event for event in events if event["name"] == "memory.model.failed"]
        assert result["status"] == "failed"
        assert len(terminal) == 1
        assert terminal[0]["payload"]["error"] == {
            "code": "MODEL_DOWNLOAD_FAILED",
            "message": "记忆模型下载失败，原缓存保持不变。",
            "retryable": True,
        }
        assert "private cache path" not in str(terminal)
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
