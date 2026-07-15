from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import app.agent.memory as memory_module
from app.agent.memory import MemoryStore
from app.core.resource_manager import ResourceRegistry


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
def test_memory_preload_prepares_shared_imports_before_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = threading.Event()
    monkeypatch.setattr(
        memory_module,
        "_prepare_memory_background_imports",
        prepared.set,
    )
    store = _BlockingMemoryStore(base_dir=tmp_path, resource_registry=ResourceRegistry())

    store.preload(wait=False)

    assert prepared.is_set()
    assert store.create_started.wait(1)
    store.allow_return.set()
    assert _wait_until(lambda: not store._thread_group.is_running())
    store.close()


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
