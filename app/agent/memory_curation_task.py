"""无 Qt 的记忆整理任务。"""

from __future__ import annotations

from typing import Any

from app.agent.memory_curator import MemoryCurator
from app.core.cancellation import CancellationToken
from app.storage.chat_history import ChatHistoryEntry


class MemoryCurationTask:
    def __init__(
        self,
        curator: MemoryCurator,
        entries: list[ChatHistoryEntry],
    ) -> None:
        self.curator = curator
        self.entries = list(entries)
        self._cancel_token = CancellationToken()

    def cancel(self) -> None:
        self._cancel_token.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_token.is_cancelled()

    def run(self) -> Any:
        self._cancel_token.throw_if_cancelled()
        result = self.curator.curate_entries(
            self.entries,
            cancel_checker=self._cancel_token.throw_if_cancelled,
        )
        self._cancel_token.throw_if_cancelled()
        return result
