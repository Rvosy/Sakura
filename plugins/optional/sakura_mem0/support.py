"""记忆插件进程使用的路径、日志与取消支持。"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
import threading
import time
from pathlib import Path
from typing import Callable


_logger: object | None = None
_operation_id: ContextVar[str] = ContextVar("memory_log_operation", default="")


def bind_logger(logger: object | None) -> None:
    global _logger
    _logger = logger


def log_event(_channel: str, message: str, attributes: object = None, *, event: str = "", severity: str = "info", **_kwargs: object) -> None:
    callback = getattr(_logger, severity, None)
    if callable(callback):
        fields = dict(attributes) if isinstance(attributes, dict) else {}
        if _operation_id.get():
            fields["operation_id"] = _operation_id.get()
        if event:
            fields["event"] = event
        callback(message, fields=fields)


@contextlib.contextmanager
def interaction_context(operation_id: str):
    token = _operation_id.set(operation_id)
    try:
        yield
    finally:
        _operation_id.reset(token)


class ThreadGroupResource:
    def __init__(self) -> None:
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
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(max(0.0, deadline - time.monotonic()))


class StoragePaths:
    """未指定宿主存储目录时使用的插件本地路径。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    @property
    def memory_dir(self) -> Path:
        return self.base_dir / "data" / "memory"

    @property
    def memory_cache_dir(self) -> Path:
        return self.base_dir / "data" / "cache" / "memory"


class OperationCancelled(RuntimeError):
    pass


def check_cancelled(checker: Callable[[], object] | None) -> None:
    if checker is not None:
        checker()
