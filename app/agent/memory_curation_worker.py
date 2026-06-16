from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.agent.memory_curator import MemoryCurator
from app.core.cancellation import CancellationToken, OperationCancelled
from app.storage.chat_history import ChatHistoryEntry


class MemoryCurationWorker(QObject):
    """在后台线程执行记忆整理，避免阻塞桌宠 UI。"""

    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        curator: MemoryCurator,
        entries: list[ChatHistoryEntry],
    ) -> None:
        super().__init__()
        self.curator = curator
        self.entries = entries
        self._cancel_token = CancellationToken()

    @Slot()
    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        try:
            self._cancel_token.throw_if_cancelled()
            result = self.curator.curate_entries(
                self.entries,
                cancel_checker=self._cancel_token.throw_if_cancelled,
            )
            self._cancel_token.throw_if_cancelled()
        except OperationCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # 后台整理失败不能影响主聊天。
            if self._cancel_token.is_cancelled():
                self.cancelled.emit()
                return
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)
