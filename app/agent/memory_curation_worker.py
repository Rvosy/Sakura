from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.agent.memory_curation_task import MemoryCurationTask
from app.agent.memory_curator import MemoryCurator
from app.core.cancellation import OperationCancelled
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
        # 保留旧 Qt 适配器的可观察属性；实际执行统一委托给无 Qt task。
        self.curator = curator
        self.entries = entries
        self.task = MemoryCurationTask(curator, entries)

    @Slot()
    def cancel(self) -> None:
        self.task.cancel()

    @Slot()
    def run(self) -> None:
        try:
            result = self.task.run()
        except OperationCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # 后台整理失败不能影响主聊天。
            if self.task.is_cancelled:
                self.cancelled.emit()
                return
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)
