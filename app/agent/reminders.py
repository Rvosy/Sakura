from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScheduledReminder:
    id: str
    text: str
    trigger_at: str
    repeat: None
    created_at: str
    completed_at: str | None = None
    cancelled_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "trigger_at": self.trigger_at,
            "repeat": self.repeat,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "cancelled_at": self.cancelled_at,
        }


class ReminderStore:
    """按 JSON 保存一次性提醒；第一版不实现重复提醒。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def add_reminder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = _required_text(arguments, "text")
        trigger_at = _normalize_trigger_at(_required_text(arguments, "trigger_at"))
        repeat = arguments.get("repeat")
        if repeat is not None:
            raise ValueError("第一版提醒暂不支持 repeat，请传 null 或省略。")

        now = _now_iso()
        reminder = ScheduledReminder(
            id=uuid.uuid4().hex[:8],
            text=text,
            trigger_at=trigger_at,
            repeat=None,
            created_at=now,
        )
        data = self._load()
        data["reminders"].append(reminder.to_dict())
        self._save(data)
        return {"reminder": reminder.to_dict()}

    def list_reminders(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        reminders = [
            reminder
            for reminder in data["reminders"]
            if _is_active_reminder(reminder)
        ]
        return {"reminders": reminders}

    def cancel_reminder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reminder_id = _required_text(arguments, "id")
        data = self._load()
        for reminder in data["reminders"]:
            if reminder.get("id") != reminder_id:
                continue
            if reminder.get("cancelled_at") is None:
                reminder["cancelled_at"] = _now_iso()
                self._save(data)
            return {"reminder": reminder}
        raise ValueError(f"未找到提醒：{reminder_id}")

    def due_reminders(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now().astimezone()
        data = self._load()
        return [
            reminder
            for reminder in data["reminders"]
            if _is_active_reminder(reminder)
            and _parse_datetime(str(reminder["trigger_at"])) <= now
        ]

    def mark_completed(self, reminder_id: str) -> dict[str, Any]:
        data = self._load()
        for reminder in data["reminders"]:
            if reminder.get("id") != reminder_id:
                continue
            if reminder.get("completed_at") is None:
                reminder["completed_at"] = _now_iso()
                self._save(data)
            return reminder
        raise ValueError(f"未找到提醒：{reminder_id}")

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {"reminders": []}

        try:
            raw_data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"提醒文件不是有效 JSON：{self.path}") from exc
        return _normalize_data(raw_data)

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        normalized = _normalize_data(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _normalize_data(raw_data: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(raw_data, dict) or not isinstance(raw_data.get("reminders", []), list):
        raise ValueError("提醒文件格式无效，顶层必须是包含 reminders 列表的对象。")
    reminders = [
        _normalize_reminder(item)
        for item in raw_data.get("reminders", [])
        if isinstance(item, dict)
    ]
    return {"reminders": [reminder for reminder in reminders if reminder is not None]}


def _normalize_reminder(item: dict[str, Any]) -> dict[str, Any] | None:
    reminder_id = item.get("id")
    text = item.get("text")
    trigger_at = item.get("trigger_at")
    created_at = item.get("created_at")
    if not all(isinstance(value, str) and value.strip() for value in (reminder_id, text, trigger_at)):
        return None
    repeat = item.get("repeat")
    if repeat is not None:
        raise ValueError("提醒文件中包含不支持的 repeat 值。")

    return {
        "id": reminder_id.strip(),
        "text": text.strip(),
        "trigger_at": _normalize_trigger_at(trigger_at),
        "repeat": None,
        "created_at": created_at.strip() if isinstance(created_at, str) and created_at.strip() else _now_iso(),
        "completed_at": _optional_datetime_text(item.get("completed_at")),
        "cancelled_at": _optional_datetime_text(item.get("cancelled_at")),
    }


def _is_active_reminder(reminder: dict[str, Any]) -> bool:
    return reminder.get("completed_at") is None and reminder.get("cancelled_at") is None


def _required_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少必填参数：{key}")
    return value.strip()


def _normalize_trigger_at(value: str) -> str:
    return _parse_datetime(value).isoformat(timespec="seconds")


def _optional_datetime_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("时间字段必须是 ISO 字符串或 null。")
    return _parse_datetime(value).isoformat(timespec="seconds")


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"时间必须是 ISO 格式：{value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
