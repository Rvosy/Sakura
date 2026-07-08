from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


class MobileChatWorker(QObject):
    """Runs a mobile bridge request only after PetWindow has serialized it."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, bridge: Any, character_id: str, text: str, image_data_url: str) -> None:
        super().__init__()
        self._bridge = bridge
        self._character_id = character_id
        self._text = text
        self._image_data_url = image_data_url

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self._bridge.execute_chat(self._character_id, self._text, self._image_data_url))
        except Exception as exc:  # noqa: BLE001 - delivered to the HTTP caller below
            self.failed.emit(str(exc))
