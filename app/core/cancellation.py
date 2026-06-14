from __future__ import annotations

import threading
import time
from collections.abc import Callable


class OperationCancelled(RuntimeError):
    """后台操作被协作取消。"""


CancelChecker = Callable[[], None]


class CancellationToken:
    """线程安全的协作取消令牌。"""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise OperationCancelled()

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            self.throw_if_cancelled()
            return
        if self._cancelled.wait(seconds):
            raise OperationCancelled()


def check_cancelled(cancel_checker: CancelChecker | None) -> None:
    if cancel_checker is not None:
        cancel_checker()


def cancellable_sleep(seconds: float, cancel_checker: CancelChecker | None) -> None:
    if cancel_checker is None:
        threading.Event().wait(max(0.0, seconds))
        return
    if seconds <= 0:
        cancel_checker()
        return
    deadline = time.monotonic() + seconds
    while True:
        cancel_checker()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        threading.Event().wait(min(remaining, 0.05))
