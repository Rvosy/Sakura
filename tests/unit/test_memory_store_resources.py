from __future__ import annotations

import builtins
import sys
import threading
import time
from collections import deque
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.agent.memory as memory_module
from app.agent.memory import (
    MemoryStore,
    ProcessIsolatedFastEmbedEmbedding,
    ProcessIsolatedMem0Client,
)
from app.core.runtime_resources import ResourceRegistry


def test_optional_background_import_absence_degrades_without_blocking_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def reject_anyio(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "anyio":
            raise ModuleNotFoundError("No module named 'anyio'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_anyio)

    assert memory_module._prepare_memory_background_imports() is False


class _FakeMemory:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _ReloadableMemory(_FakeMemory):
    def __init__(self) -> None:
        super().__init__()
        self.llm_sections: list[dict[str, object]] = []

    def reload_llm(self, llm_section: dict[str, object]) -> None:
        self.llm_sections.append(llm_section)


class _BlockingMemoryStore(MemoryStore):
    def __post_init__(self) -> None:
        self.create_started = threading.Event()
        self.allow_return = threading.Event()
        self.created: list[_FakeMemory] = []
        super().__post_init__()

    def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
        self.create_started.set()
        assert self.allow_return.wait(2)
        mem = _FakeMemory()
        self.created.append(mem)
        return mem


class _FailingMemoryStore(MemoryStore):
    def _create_memory_client(self, api_settings=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected embedding startup failure")


class _FakeEmbeddingConnection:
    def __init__(self) -> None:
        self.responses = deque([("ready", None)])
        self.sent: list[tuple[object, ...]] = []
        self.closed = False

    def poll(self, _timeout: float | None = None) -> bool:
        return bool(self.responses)

    def recv(self):  # type: ignore[no-untyped-def]
        return self.responses.popleft()

    def send(self, message):  # type: ignore[no-untyped-def]
        self.sent.append(message)
        if message[0] == "embed":
            self.responses.append(("result", [0.25, 0.75]))
        elif message[0] == "embed_batch":
            self.responses.append(("result", [[0.25, 0.75] for _ in message[1]]))

    def close(self) -> None:
        self.closed = True


class _FakeEmbeddingProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.alive = True
        self.started = False
        self.terminated = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
        return None

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def close(self) -> None:
        self.closed = True


class _FakeEmbeddingContext:
    def __init__(self) -> None:
        self.parent = _FakeEmbeddingConnection()
        self.child = _FakeEmbeddingConnection()
        self.process = _FakeEmbeddingProcess()

    def Pipe(self, *, duplex=True):  # type: ignore[no-untyped-def, no-untyped-call]
        assert duplex is True
        return self.parent, self.child

    def Process(self, **_kwargs):  # type: ignore[no-untyped-def, no-untyped-call]
        return self.process


class _RecordingChildConnection:
    def __init__(self) -> None:
        self.sent: list[tuple[object, object]] = []
        self.closed = False

    def send(self, message):  # type: ignore[no-untyped-def]
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class _FakeMem0Connection:
    def __init__(self, *, respond_to_requests: bool = True) -> None:
        self.responses = deque(
            [
                (
                    "progress",
                    {
                        "event": "mem0_import_completed",
                        "stage": "mem0_import",
                        "outcome": "completed",
                    },
                ),
                ("ready", None),
            ]
        )
        self.respond_to_requests = respond_to_requests
        self.sent: list[tuple[object, ...]] = []
        self.closed = False

    def poll(self, _timeout: float | None = None) -> bool:
        return bool(self.responses)

    def recv(self):  # type: ignore[no-untyped-def]
        return self.responses.popleft()

    def send(self, message):  # type: ignore[no-untyped-def]
        self.sent.append(message)
        if not self.respond_to_requests or message[0] != "request":
            return
        method, args, kwargs = message[1:]
        if method == "reset_curation_cache":
            result: object = {"messages": 2, "history": 1}
        elif method == "reload_llm":
            result = None
        else:
            result = {"method": method, "args": args, "kwargs": kwargs}
        self.responses.append(("result", result))

    def close(self) -> None:
        self.closed = True


class _FakeMem0Context:
    def __init__(self, *, respond_to_requests: bool = True) -> None:
        self.parent = _FakeMem0Connection(respond_to_requests=respond_to_requests)
        self.child = _RecordingChildConnection()
        self.process = _FakeEmbeddingProcess()

    def Pipe(self, *, duplex=True):  # type: ignore[no-untyped-def, no-untyped-call]
        assert duplex is True
        return self.parent, self.child

    def Process(self, **_kwargs):  # type: ignore[no-untyped-def, no-untyped-call]
        return self.process


@pytest.mark.allow_memory_preload
def test_memory_preload_thread_group_tracks_loader(tmp_path: Path) -> None:
    registry = ResourceRegistry()
    store = _BlockingMemoryStore(base_dir=tmp_path, resource_registry=registry)

    store.preload(wait=False)
    assert store.create_started.wait(1)

    assert store._thread_group in registry._resources
    assert store._thread_group.is_running() is True

    store.allow_return.set()
    assert _wait_until(lambda: not store._thread_group.is_running())
    store.close()


@pytest.mark.allow_memory_preload
def test_memory_preload_never_imports_shared_dependencies_on_request_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_attempted = threading.Event()

    def fail_late_import() -> None:
        import_attempted.set()
        raise AssertionError("shared dependencies must be ready before Memory RPC")

    monkeypatch.setattr(
        memory_module,
        "_prepare_memory_background_imports",
        fail_late_import,
    )
    store = _BlockingMemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())

    store.preload(wait=False)

    assert import_attempted.is_set() is False
    assert store.create_started.wait(1)
    store.allow_return.set()
    assert _wait_until(lambda: not store._thread_group.is_running())
    store.close()


@pytest.mark.allow_memory_preload
def test_memory_preload_publishes_cached_model_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_module, "_embedding_model_cached", lambda *_args: True)
    log_path = tmp_path / "data" / "logs" / memory_module.MEMORY_INITIALIZATION_LOG_NAME
    log_path.parent.mkdir(parents=True)
    log_path.write_text("", encoding="utf-8")
    store = _FailingMemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())
    statuses: list[tuple[str, str]] = []
    store.add_status_listener(lambda status, message: statuses.append((status, message)))

    store.preload(wait=False)

    assert _wait_until(lambda: not store._loading)
    assert statuses
    assert statuses[-1][0] == "failed"
    diagnostic = store.load_diagnostic()
    assert diagnostic["outcome"] == "failed"
    assert diagnostic["errorType"] == "RuntimeError"
    assert "injected" not in str(diagnostic)
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "memory_store_load_failed" for event in events)
    assert "injected embedding startup failure" not in log_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in log_path.read_text(encoding="utf-8")
    store.close()


def test_fastembed_child_reports_fixed_startup_diagnostic_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingChildConnection()
    real_import = builtins.__import__

    def reject_fastembed(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "fastembed":
            raise ModuleNotFoundError("PRIVATE C:\\Users\\owner\\model")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_fastembed)

    memory_module._run_process_isolated_fastembed_embedding(
        connection,
        {"model": "fixed-model", "model_kwargs": {}},
    )

    kind, payload = connection.sent[-1]
    assert kind == "startup_error"
    assert payload == {
        "event": "embedding_startup_failed",
        "stage": "dependency_import",
        "outcome": "failed",
        "category": "dependency_import_failed",
        "errorType": "ModuleNotFoundError",
    }
    assert "PRIVATE" not in str(connection.sent)
    assert "Users" not in str(connection.sent)
    assert connection.closed is True


def test_mem0_fastembed_adapter_uses_local_onnx_and_normalizes_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], int]] = []

    class FakeTextEmbedding:
        embedding_size = memory_module.DEFAULT_EMBEDDING_DIMS

        def __init__(self, *, model_name: str, **kwargs: object) -> None:
            assert model_name == memory_module.DEFAULT_EMBEDDING_MODEL
            assert kwargs == {
                "specific_model_path": "fixed-snapshot",
                "local_files_only": True,
                "providers": ["CPUExecutionProvider"],
            }

        def embed(self, documents, batch_size=256):  # type: ignore[no-untyped-def]
            values = list(documents)
            calls.append((values, batch_size))
            for index, _value in enumerate(values, start=1):
                yield [float(index), 0.5]

    monkeypatch.setitem(
        sys.modules,
        "fastembed",
        SimpleNamespace(TextEmbedding=FakeTextEmbedding),
    )
    connection = _RecordingChildConnection()
    monkeypatch.setattr(memory_module, "_MEM0_CHILD_CONNECTION", connection)
    config = SimpleNamespace(
        model=memory_module.DEFAULT_EMBEDDING_MODEL,
        embedding_dims=memory_module.DEFAULT_EMBEDDING_DIMS,
        model_kwargs={
            "specific_model_path": "fixed-snapshot",
            "local_files_only": True,
            "providers": ["CPUExecutionProvider"],
        },
    )

    embedding = memory_module._ProcessLocalDiagnosticFastEmbedEmbedding(config)

    assert embedding.embed("one\nline", "search") == [1.0, 0.5]
    assert embedding.embed_batch(["first\nline", "second"], "add") == [
        [1.0, 0.5],
        [2.0, 0.5],
    ]
    assert calls == [(["one line"], 1), (["first line", "second"], 256)]
    assert [message[1]["event"] for message in connection.sent] == [
        "embedding_dependency_import_started",
        "embedding_dependency_import_completed",
        "embedding_model_load_started",
        "embedding_model_load_completed",
    ]


def test_memory_model_cache_requires_pinned_onnx_snapshot_and_ignores_old_torch(
    tmp_path: Path,
) -> None:
    old_snapshot = (
        tmp_path
        / "runtime"
        / "hf-cache"
        / "hub"
        / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
        / "old"
    )
    old_snapshot.mkdir(parents=True)
    (old_snapshot / "model.safetensors").write_bytes(b"old")

    assert memory_module._embedding_model_cached(
        memory_module.DEFAULT_EMBEDDING_MODEL,
        tmp_path,
    ) is False

    snapshot = (
        tmp_path
        / "runtime"
        / "fastembed-cache"
        / memory_module.DEFAULT_EMBEDDING_MODEL_CACHE_NAME
        / "snapshots"
        / memory_module.DEFAULT_EMBEDDING_ARTIFACT_REVISION
    )
    snapshot.mkdir(parents=True)
    for filename in memory_module.DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES:
        (snapshot / filename).write_bytes(b"onnx" if filename == "model.onnx" else b"{}")

    assert memory_module._embedding_model_cached(
        memory_module.DEFAULT_EMBEDDING_MODEL,
        tmp_path,
    ) is True
    kwargs = memory_module._local_embedding_model_kwargs(
        memory_module.DEFAULT_EMBEDDING_MODEL,
        tmp_path,
    )
    assert kwargs["specific_model_path"] == str(snapshot)
    assert kwargs["local_files_only"] is True
    assert kwargs["providers"] == ["CPUExecutionProvider"]


def test_memory_initialization_diagnostic_stops_at_the_fixed_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_module, "MEMORY_INITIALIZATION_LOG_MAX_BYTES", 220)
    path = tmp_path / "data" / "logs" / memory_module.MEMORY_INITIALIZATION_LOG_NAME
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")

    for _ in range(10):
        memory_module.append_memory_initialization_diagnostic(
            tmp_path,
            component="memory_store",
            event="bounded_event",
            stage="test",
            outcome="completed",
        )

    assert 0 < path.stat().st_size <= 220
    assert all(json.loads(line)["event"] == "bounded_event" for line in path.read_text().splitlines())


def test_process_isolated_fastembed_keeps_native_protocol_out_of_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeEmbeddingContext()
    monkeypatch.setattr(memory_module.multiprocessing, "get_context", lambda _method: context)
    config = SimpleNamespace(
        model="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={"local_files_only": True},
    )

    embedding = ProcessIsolatedFastEmbedEmbedding(config)
    embedding.wait_ready(timeout=1)

    assert embedding.embed("中文", "search") == [0.25, 0.75]
    assert embedding.embed_batch(["中文", "日本語"], "add") == [
        [0.25, 0.75],
        [0.25, 0.75],
    ]
    embedding.close()

    assert context.process.started is True
    assert context.process.terminated is True
    assert context.process.closed is True
    assert context.parent.closed is True


def test_process_isolated_mem0_client_routes_runtime_operations_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeMem0Context()
    monkeypatch.setattr(memory_module.multiprocessing, "get_context", lambda _method: context)
    diagnostics: list[dict[str, object]] = []

    client = ProcessIsolatedMem0Client({"fixed": "config"})
    client.set_diagnostic_listener(diagnostics.append)
    client.wait_ready(timeout=1)

    assert client.get_all(filters={"user_id": "sakura"})["method"] == "get_all"
    assert client.search("query", filters={"user_id": "sakura"})["method"] == "search"
    assert client.add("memory", user_id="sakura")["method"] == "add"
    assert client.get("memory-id")["method"] == "get"
    assert client.update("memory-id", "updated")["method"] == "update"
    assert client.delete("memory-id")["method"] == "delete"
    assert client.reset_curation_cache(
        scope_id="sakura",
        memory_ids=["memory-id"],
    ) == {"messages": 2, "history": 1}
    client.reload_llm({"provider": "openai", "config": {"model": "fixed-model"}})
    client.close()

    requests = [message[1] for message in context.parent.sent if message[0] == "request"]
    assert requests == [
        "get_all",
        "search",
        "add",
        "get",
        "update",
        "delete",
        "reset_curation_cache",
        "reload_llm",
    ]
    assert diagnostics[-1]["event"] == "mem0_ready"
    assert diagnostics[-1]["component"] == "mem0_process"
    assert diagnostics[-1]["childPid"] == 4242
    assert context.process.started is True
    assert context.process.terminated is True
    assert context.process.closed is True
    assert context.parent.closed is True


def test_process_isolated_mem0_request_timeout_terminates_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeMem0Context(respond_to_requests=False)
    monkeypatch.setattr(memory_module.multiprocessing, "get_context", lambda _method: context)
    monkeypatch.setattr(ProcessIsolatedMem0Client, "REQUEST_TIMEOUT_SECONDS", 0.0)
    client = ProcessIsolatedMem0Client({"fixed": "config"})
    client.wait_ready(timeout=1)

    with pytest.raises(TimeoutError, match="请求超时"):
        client.search("query")

    assert context.process.terminated is True
    assert context.process.closed is True
    assert context.parent.closed is True


def test_process_isolated_mem0_startup_timeout_terminates_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeMem0Context()
    context.parent.responses.clear()
    monkeypatch.setattr(memory_module.multiprocessing, "get_context", lambda _method: context)
    client = ProcessIsolatedMem0Client({"fixed": "config"})

    with pytest.raises(RuntimeError, match="初始化超时"):
        client.wait_ready(timeout=0)

    diagnostic = client.load_diagnostic()
    assert diagnostic["category"] == "startup_timeout"
    assert diagnostic["errorType"] == "TimeoutError"
    assert context.process.terminated is True
    assert context.process.closed is True
    assert context.parent.closed is True


def test_mem0_child_reports_fixed_startup_diagnostic_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingChildConnection()
    real_import = builtins.__import__

    def reject_mem0(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "mem0":
            raise ModuleNotFoundError("PRIVATE C:\\Users\\owner\\memory")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_mem0)

    memory_module._run_process_isolated_mem0_client(connection, {"fixed": "config"})

    kind, payload = connection.sent[-1]
    assert kind == "startup_error"
    assert payload == {
        "event": "mem0_startup_failed",
        "stage": "mem0_import",
        "outcome": "failed",
        "category": "dependency_import_failed",
        "errorType": "ModuleNotFoundError",
    }
    assert "PRIVATE" not in str(connection.sent)
    assert "Users" not in str(connection.sent)
    assert connection.closed is True


@pytest.mark.parametrize(
    ("event_prefix", "stage"),
    [
        ("qdrant_create", "qdrant_create"),
        ("llm_create", "llm_create"),
        ("sqlite_create", "sqlite_create"),
    ],
)
def test_mem0_component_creation_reports_exact_safe_stage(
    monkeypatch: pytest.MonkeyPatch,
    event_prefix: str,
    stage: str,
) -> None:
    connection = _RecordingChildConnection()
    monkeypatch.setattr(memory_module, "_MEM0_CHILD_CONNECTION", connection)
    marker = object()

    result = memory_module._create_process_isolated_mem0_component(
        event_prefix=event_prefix,
        stage=stage,
        factory=lambda: marker,
    )

    assert result is marker
    assert connection.sent == [
        (
            "progress",
            {"event": f"{event_prefix}_started", "stage": stage, "outcome": "started"},
        ),
        (
            "progress",
            {"event": f"{event_prefix}_completed", "stage": stage, "outcome": "completed"},
        ),
    ]

    connection.sent.clear()

    def fail() -> object:
        raise ImportError("PRIVATE C:\\Users\\owner\\dependency")

    with pytest.raises(ImportError, match="PRIVATE"):
        memory_module._create_process_isolated_mem0_component(
            event_prefix=event_prefix,
            stage=stage,
            factory=fail,
        )

    assert connection.sent == [
        (
            "progress",
            {"event": f"{event_prefix}_started", "stage": stage, "outcome": "started"},
        ),
        (
            "progress",
            {
                "event": f"{event_prefix}_failed",
                "stage": stage,
                "outcome": "failed",
                "category": "dependency_import_failed",
                "errorType": "ImportError",
            },
        ),
    ]
    assert "PRIVATE" not in str(connection.sent)
    assert "Users" not in str(connection.sent)


@pytest.mark.allow_memory_preload
def test_memory_reload_thread_group_tracks_reloader(tmp_path: Path) -> None:
    registry = ResourceRegistry()
    old_memory = _FakeMemory()
    store = _BlockingMemoryStore(
        base_dir=tmp_path,
        memory_client=old_memory,
        resource_registry=registry,
    )

    store.reload_api_settings(object(), wait=False)  # type: ignore[arg-type]
    assert store.create_started.wait(1)

    assert store._thread_group in registry._resources
    assert store._thread_group.is_running() is True

    store.allow_return.set()
    assert _wait_until(lambda: not store._thread_group.is_running())
    assert store.is_ready() is True
    store.close()


def test_memory_reload_updates_isolated_client_llm_without_reopening_storage(
    tmp_path: Path,
) -> None:
    memory = _ReloadableMemory()
    store = MemoryStore(
        base_dir=tmp_path,
        memory_client=memory,
        resource_registry=ResourceRegistry(),
    )
    settings = SimpleNamespace(
        model="fixed-model",
        api_key="PRIVATE_NOT_LOGGED",
        base_url="https://provider.invalid/v1/",
    )

    store.reload_api_settings(settings, wait=True)  # type: ignore[arg-type]

    assert store._memory is memory
    assert memory.llm_sections == [
        {
            "provider": "openai",
            "config": {
                "model": "fixed-model",
                "temperature": 0.1,
                "max_tokens": 2000,
                "api_key": "PRIVATE_NOT_LOGGED",
                "openai_base_url": "https://provider.invalid/v1",
            },
        }
    ]
    store.close()


def test_memory_config_projection_does_not_open_qdrant_storage(tmp_path: Path) -> None:
    store = MemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())
    qdrant_path = tmp_path / "data" / "memory" / "qdrant"

    config = store.build_mem0_config()

    assert config["vector_store"]["config"]["path"] == qdrant_path.as_posix()
    assert config["embedder"]["provider"] == "fastembed"
    assert config["embedder"]["config"]["model"] == memory_module.DEFAULT_EMBEDDING_MODEL
    model_kwargs = config["embedder"]["config"]["model_kwargs"]
    assert model_kwargs["local_files_only"] is True
    assert model_kwargs["providers"] == ["CPUExecutionProvider"]
    assert model_kwargs["specific_model_path"].endswith(
        str(
            Path(memory_module.DEFAULT_EMBEDDING_MODEL_CACHE_NAME)
            / "snapshots"
            / memory_module.DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )
    )
    assert qdrant_path.exists() is False
    store.close()


@pytest.mark.allow_memory_preload
def test_memory_close_invalidates_late_loader_and_closes_runtime(tmp_path: Path) -> None:
    store = _BlockingMemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())
    store.preload(wait=False)
    assert store.create_started.wait(1)

    close_thread = threading.Thread(target=store.close, daemon=True)
    close_thread.start()
    assert _wait_until(lambda: store._closed)

    store.allow_return.set()
    close_thread.join(2)

    assert not close_thread.is_alive()
    assert store.created
    assert store.created[0].close_count == 1
    assert store.is_ready() is False


@pytest.mark.allow_memory_preload
def test_memory_close_blocks_wait_preload_from_restarting(tmp_path: Path) -> None:
    store = _BlockingMemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())

    store.close()

    with pytest.raises(RuntimeError, match="已关闭"):
        store.preload(wait=True)
    assert store.create_started.is_set() is False


def _wait_until(predicate, timeout_s: float = 1.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
