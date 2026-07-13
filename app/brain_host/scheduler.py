"""Brain Host 使用的标准线程周期调度器。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.runtime_log import log_event


@dataclass
class _ScheduledJob:
    name: str
    interval: float
    callback: Callable[[], None]
    next_run: float


class PeriodicScheduler:
    def __init__(self, *, poll_interval: float = 0.25) -> None:
        self.poll_interval = max(0.001, float(poll_interval))
        self._jobs: dict[str, _ScheduledJob] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def add_job(
        self,
        name: str,
        interval: float,
        callback: Callable[[], None],
        *,
        run_immediately: bool = False,
    ) -> None:
        normalized_interval = max(0.001, float(interval))
        now = time.monotonic()
        with self._lock:
            self._jobs[name] = _ScheduledJob(
                name=name,
                interval=normalized_interval,
                callback=callback,
                next_run=now if run_immediately else now + normalized_interval,
            )

    def remove_job(self, name: str) -> bool:
        with self._lock:
            return self._jobs.pop(name, None) is not None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="sakura-brain-scheduler",
                daemon=False,
            )
            self._thread.start()

    def stop(self, timeout: float | None = None) -> bool:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        stopped = not thread.is_alive()
        if stopped:
            self._thread = None
        return stopped

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now = time.monotonic()
            with self._lock:
                due = [job for job in self._jobs.values() if job.next_run <= now]
                for job in due:
                    job.next_run = now + job.interval
            for job in due:
                if self._stop_event.is_set():
                    break
                try:
                    job.callback()
                except Exception as exc:  # noqa: BLE001
                    log_event(
                        "BrainScheduler",
                        "周期任务执行失败",
                        {"job": job.name, "error": str(exc)},
                    )
            self._stop_event.wait(self.poll_interval)
