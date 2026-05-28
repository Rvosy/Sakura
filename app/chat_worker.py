from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.api_client import OpenAICompatibleClient
from app.chat_reply import ChatReply


class ChatWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        api_client: OpenAICompatibleClient,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> None:
        super().__init__()
        self.api_client = api_client
        self.system_prompt = system_prompt
        self.messages = messages

    @Slot()
    def run(self) -> None:
        try:
            reply: ChatReply = self.api_client.chat(self.system_prompt, self.messages)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
            return
        self.finished.emit(reply)
