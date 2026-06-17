from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.pet_state.models import (
    PetStateRecord,
    apply_pet_state_delta,
    default_pet_state_record,
    pet_state_record_from_dict,
)
from app.storage.atomic import atomic_write_text


class PetStateStore(QObject):
    """本地桌宠状态存储。

    Store 由宿主持有，模型只能通过工具提交 delta；前端通过 state_changed
    接收已校验、已持久化后的快照。
    """

    state_changed = Signal(object)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self._lock = RLock()
        self._record = self._load()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._record.to_dict()

    def update_from_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("pet_state_update 参数必须是 JSON object。")
        delta = arguments.get("delta")
        if not isinstance(delta, dict):
            raise ValueError("pet_state_update.delta 必须是 JSON object。")
        force_fields = arguments.get("force_fields")
        if force_fields is not None and not isinstance(force_fields, list):
            raise ValueError("pet_state_update.force_fields 必须是字符串数组。")
        forced = bool(arguments.get("forced", False))
        force_reason = str(arguments.get("force_reason") or "")
        with self._lock:
            next_record, decision = apply_pet_state_delta(
                self._record,
                delta,
                forced=forced,
                force_fields=force_fields,
                force_reason=force_reason,
            )
            self._record = next_record
            snapshot = self._record.to_dict()
            self._save_locked()
        self.state_changed.emit(snapshot)
        return {
            "state": snapshot["state"],
            "accepted": True,
            "harness_decision": decision,
        }

    def _load(self) -> PetStateRecord:
        if not self.path.exists():
            return default_pet_state_record()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_pet_state_record()
        if not isinstance(data, dict):
            return default_pet_state_record()
        try:
            return pet_state_record_from_dict(data)
        except ValueError:
            return default_pet_state_record()

    def _save_locked(self) -> None:
        atomic_write_text(
            self.path,
            json.dumps(self._record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
