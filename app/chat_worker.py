from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.agent import AgentResult, AgentRuntime


class ChatWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        messages: list[dict[str, str]],
    ) -> None:
        super().__init__()
        self.agent_runtime = agent_runtime
        self.messages = messages

    @Slot()
    def run(self) -> None:
        try:
            result: AgentResult = self.agent_runtime.handle_user_message(self.messages)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)
