from __future__ import annotations

import threading
import time
import asyncio
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


async def await_cancellable(awaitable, cancel_checker: CancelChecker | None):
    """等待网络操作时响应取消，并等待底层任务关闭连接。"""
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.05)
            check_cancelled(cancel_checker)
        check_cancelled(cancel_checker)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
