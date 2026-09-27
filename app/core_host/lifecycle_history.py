"""Persist confirmed Core session boundaries as character-scoped Timeline facts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.core.runtime_log import log_event
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore


class LifecycleHistory:
    def __init__(self, user_root: Path, generation_number: int) -> None:
        self._store = TimelineStore(StoragePaths(user_root).timeline_database())
        self._generation_number = generation_number
        self._lock = Lock()
        self._started = False
        self._closed = False

    def start(self, character_id: str) -> None:
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
            if self._generation_number == 1:
                self._append(character_id, "app.started", "桌宠已启动")
            else:
                self._append(character_id, "app.reconnected", "桌宠运行服务已重新连接；这不表示用户关闭后重新打开了应用。")

    def finish(self, character_id: str | None) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._started and character_id:
                self._append(character_id, "app.closed", "桌宠正在退出")

    def _append(self, character_id: str, event_type: str, text: str) -> None:
        try:
            self._store.initialize()
            self._store.append(NewTimelineEntry(
                entry_id=uuid4().hex, turn_id=uuid4().hex, character_id=character_id,
                kind=TimelineKind.SYSTEM, origin="host", created_at=datetime.now().astimezone().isoformat(),
                payload={"text": text, "eventType": event_type},
            ))
        except Exception as error:
            # A history write must not prevent startup or orderly resource cleanup.
            log_event("Timeline", "桌宠会话记录写入失败", {
                "event_type": event_type, "error_type": type(error).__name__,
                "diagnostic": str(error), "reason_code": "LIFECYCLE_HISTORY_WRITE_FAILED",
            }, severity="error")
