"""Brain Host 会话内待确认动作表。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from app.agent.actions import PendingToolAction


class PendingActionLookupError(LookupError):
    pass


DEFAULT_PENDING_ACTION_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class StoredPendingAction:
    action: PendingToolAction
    session_id: str
    interaction_id: str
    expires_at: float


class PendingActionStore:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_PENDING_ACTION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("pending action TTL must be positive")
        self._items: dict[str, StoredPendingAction] = {}
        self._lock = threading.RLock()
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock

    def add(
        self,
        action: PendingToolAction,
        *,
        session_id: str,
        interaction_id: str,
    ) -> None:
        with self._lock:
            self._purge_expired_locked()
            if action.id in self._items:
                raise PendingActionLookupError(f"duplicate pending action ID: {action.id}")
            self._items[action.id] = StoredPendingAction(
                action,
                session_id,
                interaction_id,
                self._clock() + self._ttl_seconds,
            )

    def take(self, action_id: str, *, session_id: str) -> PendingToolAction:
        with self._lock:
            stored = self._get_locked(action_id, session_id=session_id)
            del self._items[action_id]
            return stored.action

    def get(self, action_id: str, *, session_id: str) -> PendingToolAction:
        with self._lock:
            return self._get_locked(action_id, session_id=session_id).action

    def discard(self, action_id: str, *, session_id: str) -> PendingToolAction:
        return self.take(action_id, session_id=session_id)

    def list_for_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        with self._lock:
            self._purge_expired_locked()
            return tuple(
                stored.action.to_dict()
                for stored in self._items.values()
                if stored.session_id == session_id
            )

    def clear_session(self, session_id: str) -> tuple[str, ...]:
        with self._lock:
            removed = tuple(
                action_id
                for action_id, stored in self._items.items()
                if stored.session_id == session_id
            )
            for action_id in removed:
                self._items.pop(action_id, None)
            return removed

    def _get_locked(self, action_id: str, *, session_id: str) -> StoredPendingAction:
        self._purge_expired_locked()
        stored = self._items.get(action_id)
        if stored is None or stored.session_id != session_id:
            raise PendingActionLookupError("pending action does not exist in this session")
        return stored

    def _purge_expired_locked(self) -> None:
        now = self._clock()
        expired = [
            action_id
            for action_id, stored in self._items.items()
            if stored.expires_at <= now
        ]
        for action_id in expired:
            self._items.pop(action_id, None)
