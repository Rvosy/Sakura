"""Core-owned screen sampling and proactive-turn policy.

The shell reports activity and capture results; only this object owns clocks and
batch decisions. The revision rejects capture results from a discarded cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import monotonic

from app.agent.screen_awareness import ScreenAwarenessSettings


class ScreenAwarenessPolicy:
    def __init__(
        self, settings: ScreenAwarenessSettings, *, clock: Callable[[], float] = monotonic,
    ) -> None:
        self._clock = clock
        self._settings = settings.normalized()
        self._session = ""
        self._revision = 0
        self.reset()

    def reset(self, settings: ScreenAwarenessSettings | None = None) -> None:
        if settings is not None:
            self._settings = settings.normalized()
        self._revision += 1
        self._last_activity = self._last_capture = self._clock()
        self._batch_started: float | None = None
        self._count = 0
        self._clear_pending = True

    def step(self, facts: Mapping[str, object]) -> dict[str, object]:
        timestamp = self._clock()
        if facts["sessionId"] != self._session or facts.get("reset"):
            self._session = str(facts["sessionId"])
            self.reset()
        if self._clear_pending:
            self._clear_pending = False
            return {"action": "clear", "revision": self._revision}
        if facts["activity"]:
            self._last_activity = timestamp
        settings = self._settings
        if not settings.allows_screen_context():
            return {"action": "none", "revision": self._revision}
        if "count" in facts:
            if facts["revision"] != self._revision:
                return {"action": "clear", "revision": self._revision}
            if self._count == 0:
                self._batch_started = timestamp
            self._count = int(facts["count"])
        if not facts["idle"]:
            return {"action": "none", "revision": self._revision}
        interval = settings.check_interval_minutes * 60
        if "count" not in facts and min(
            timestamp - self._last_activity, timestamp - self._last_capture,
        ) >= interval:
            self._last_capture = timestamp
            return {
                "action": "capture", "revision": self._revision,
                "resolution": settings.screen_context_resolution,
                "batchLimit": settings.screen_context_batch_limit,
            }
        if (
            self._count and self._batch_started is not None
            and timestamp - self._batch_started >= settings.cooldown_minutes * 60
        ):
            return {"action": "submit", "revision": self._revision, "count": self._count}
        return {"action": "none", "revision": self._revision}
