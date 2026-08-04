from __future__ import annotations

import builtins
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.agent.memory as memory_module
from app.agent.memory import MemoryStore, ProcessIsolatedHuggingFaceEmbedding
from app.core.resource_manager import ResourceRegistry


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
    store = _FailingMemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())
    statuses: list[tuple[str, str]] = []
    store.add_status_listener(lambda status, message: statuses.append((status, message)))

    store.preload(wait=False)

    assert _wait_until(lambda: not store._loading)
    assert statuses
    assert statuses[-1][0] == "failed"
    store.close()


def test_process_isolated_embedding_keeps_torch_protocol_out_of_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _FakeEmbeddingContext()
    monkeypatch.setattr(memory_module.multiprocessing, "get_context", lambda _method: context)
    config = SimpleNamespace(
        model="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={"local_files_only": True},
    )

    embedding = ProcessIsolatedHuggingFaceEmbedding(config)
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
