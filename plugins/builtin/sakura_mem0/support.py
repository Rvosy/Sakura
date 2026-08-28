"""Small stdlib support owned by the Mem0 plugin process."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable


def log_event(*_args: object, **_kwargs: object) -> None:
    return None


def external_runtime_sink_active() -> bool:
    return False


def suppress_runtime_logs() -> contextlib.AbstractContextManager[None]:
    return contextlib.nullcontext()


def interaction_context(_operation_id: str) -> contextlib.AbstractContextManager[None]:
    return contextlib.nullcontext()


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    backup: bool = False,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.exists():
        try:
            target.with_name(target.name + ".bak").write_bytes(target.read_bytes())
        except OSError:
            pass
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def rename_with_retry(source: Path, target: Path) -> None:
    for attempt in range(5):
        try:
            Path(source).rename(target)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32} or attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


def validate_zip_resource_limits(
    archive: zipfile.ZipFile,
    *,
    destination: Path,
    label: str,
) -> int:
    members = archive.infolist()
    if len(members) > 4096:
        raise ValueError(f"{label}文件数量过多。")
    total = 0
    for member in members:
        if member.is_dir():
            continue
        if member.file_size < 0 or member.compress_size < 0:
            raise ValueError(f"{label}包含无效 ZIP 元数据。")
        if member.file_size > 2 * 1024 * 1024 * 1024:
            raise ValueError(f"{label}单个文件过大。")
        total += member.file_size
        if total > 8 * 1024 * 1024 * 1024:
            raise ValueError(f"{label}展开后总大小超过限制。")
        if (
            member.file_size > 1024 * 1024
            and member.file_size / max(1, member.compress_size) > 200
        ):
            raise ValueError(f"{label}压缩比异常。")
    cursor = Path(destination)
    while not cursor.exists() and cursor != cursor.parent:
        cursor = cursor.parent
    if shutil.disk_usage(cursor).free < total + 512 * 1024 * 1024:
        raise ValueError(f"{label}目标磁盘空间不足。")
    return total


class ThreadGroupResource:
    def __init__(self, cancel: Callable[[], object] | None = None) -> None:
        self._cancel = cancel
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stopped = False

    def spawn(
        self,
        target: Callable[[], object],
        *,
        name: str,
        daemon: bool = False,
    ) -> threading.Thread | None:
        with self._lock:
            if self._stopped:
                return None
            thread = threading.Thread(target=target, name=name, daemon=daemon)
            self._threads.append(thread)
        try:
            thread.start()
        except Exception:
            with self._lock:
                self._threads.remove(thread)
            return None
        return thread

    def stop(self, timeout_ms: int) -> None:
        with self._lock:
            self._stopped = True
            threads = list(self._threads)
        if self._cancel is not None:
            self._cancel()
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(max(0.0, deadline - time.monotonic()))


class ResourceRegistry:
    def __init__(self) -> None:
        self._groups: list[ThreadGroupResource] = []

    def track_thread_group(
        self,
        *,
        cancel: Callable[[], object] | None = None,
        label: str = "",
        shutdown_order: int = 0,
    ) -> ThreadGroupResource:
        del label, shutdown_order
        group = ThreadGroupResource(cancel)
        self._groups.append(group)
        return group

    def stop_all(self, timeout_ms: int) -> None:
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for group in reversed(self._groups):
            group.stop(max(0, int((deadline - time.monotonic()) * 1000)))


class StoragePaths:
    """Default plugin-local layout used when no Host storage root is supplied."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "data" / "logs"

    @property
    def memory_dir(self) -> Path:
        return self.base_dir / "data" / "memory"

    @property
    def memory_cache_dir(self) -> Path:
        return self.base_dir / "data" / "cache" / "memory"

    def memory_core_profiles(self) -> Path:
        return self.memory_dir / "core_profiles.json"

    def memory_curation_state(self, character_id: str) -> Path:
        safe = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in str(character_id)
        )
        return self.memory_dir / "curation_state" / f"{safe}.json"

class OperationCancelled(RuntimeError):
    pass


def check_cancelled(checker: Callable[[], object] | None) -> None:
    if checker is not None:
        checker()


CancelChecker = Callable[[], object]


__all__ = [
    "CancelChecker",
    "OperationCancelled",
    "ResourceRegistry",
    "StoragePaths",
    "ThreadGroupResource",
    "atomic_write_text",
    "check_cancelled",
    "external_runtime_sink_active",
    "interaction_context",
    "log_event",
    "rename_with_retry",
    "suppress_runtime_logs",
    "validate_zip_resource_limits",
]
