from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


GUI_LOG_SCOPE_PROGRAM = "program"
GUI_LOG_SCOPE_TTS = "tts"
GUI_LOG_LEVEL_INFO = "info"
GUI_LOG_LEVEL_WARNING = "warning"
GUI_LOG_LEVEL_ERROR = "error"
DEFAULT_GUI_LOG_SCOPE_LIMIT = 200

_SENSITIVE_KEY_MARKERS = ("api_key", "authorization", "token", "secret", "password")
_PRIVATE_TEXT_KEY_MARKERS = (
    "body",
    "content",
    "input",
    "messages",
    "output",
    "payload",
    "prompt",
    "query",
    "reply",
    "response",
    "system_prompt",
    "text",
    "translation",
    "arguments",
    "tool_calls",
)
_MAX_DETAIL_TEXT_CHARS = 180
_MAX_DETAIL_ITEMS = 8
_MAX_DETAIL_KEYS = 16
_TEXT_PREVIEW_MAX_CHARS = 60


@dataclass(frozen=True)
class GuiLogRecord:
    """展示到 GUI 的精简运行日志记录。"""

    record_id: int
    timestamp: str
    scope: str
    level: str
    category: str
    message: str
    detail: str = ""
    # 合成/播放相关记录附带的文本内容预览，仅用于界面展示
    text_preview: str = ""
    # 非空时，同 scope 相邻的同 merge_key 记录会原地替换（用于推理进度刷新）
    merge_key: str = ""


class GuiLogBuffer:
    """线程安全的内存环形日志缓冲；只保留本次会话。"""

    def __init__(self, *, max_records_per_scope: int = DEFAULT_GUI_LOG_SCOPE_LIMIT) -> None:
        self.max_records_per_scope = max(1, int(max_records_per_scope))
        self._records: dict[str, list[GuiLogRecord]] = {
            GUI_LOG_SCOPE_PROGRAM: [],
            GUI_LOG_SCOPE_TTS: [],
        }
        self._next_id = 1
        self._lock = threading.Lock()

    def append(
        self,
        *,
        timestamp: str,
        scope: str,
        level: str,
        category: str,
        message: str,
        detail: str = "",
        text_preview: str = "",
        merge_key: str = "",
    ) -> GuiLogRecord:
        normalized_scope = scope if scope in self._records else GUI_LOG_SCOPE_PROGRAM
        with self._lock:
            record = GuiLogRecord(
                record_id=self._next_id,
                timestamp=timestamp,
                scope=normalized_scope,
                level=level,
                category=category,
                message=message,
                detail=detail,
                text_preview=text_preview,
                merge_key=merge_key,
            )
            self._next_id += 1
            scope_records = self._records[normalized_scope]
            # 进度类记录原地替换上一条，避免高频刷新挤掉环形缓冲里的其他日志
            if (
                merge_key
                and scope_records
                and scope_records[-1].merge_key == merge_key
            ):
                scope_records[-1] = record
            else:
                scope_records.append(record)
            if len(scope_records) > self.max_records_per_scope:
                del scope_records[: len(scope_records) - self.max_records_per_scope]
            return record

    def snapshot(
        self,
        *,
        scope: str | None = None,
        after_id: int = 0,
    ) -> list[GuiLogRecord]:
        with self._lock:
            if scope is not None:
                records = list(self._records.get(scope, []))
            else:
                records = [
                    record
                    for scope_records in self._records.values()
                    for record in scope_records
                ]
            return sorted(
                (record for record in records if record.record_id > after_id),
                key=lambda record: record.record_id,
            )

    def clear(self, *, scope: str | None = None) -> None:
        with self._lock:
            if scope is None:
                for scope_records in self._records.values():
                    scope_records.clear()
                return
            self._records.get(scope, []).clear()


_GLOBAL_GUI_LOG_BUFFER = GuiLogBuffer()


def get_gui_log_buffer() -> GuiLogBuffer:
    return _GLOBAL_GUI_LOG_BUFFER


def clear_gui_logs() -> None:
    _GLOBAL_GUI_LOG_BUFFER.clear()


def record_log_event_for_gui(event: Any) -> GuiLogRecord | None:
    """把统一运行事件写入 GUI 环形缓冲。"""

    category_text = _category_label(str(getattr(event, "channel", "") or "runtime"))
    message_text = str(getattr(event, "message", "")).strip()
    if not message_text:
        return None
    attributes = getattr(event, "attributes", None)
    scope = _scope_for_event(event)
    level = _level_from_event(event)
    detail = _format_detail(attributes)
    merge_key = str(getattr(event, "event", "")) if getattr(event, "event", "") == "tts.service.progress" else ""
    timestamp = str(getattr(event, "timestamp", "") or _now_iso())
    text_preview = _tts_text_preview(category_text, attributes)
    record = _GLOBAL_GUI_LOG_BUFFER.append(
        timestamp=timestamp,
        scope=scope,
        level=level,
        category=category_text,
        message=message_text,
        detail=detail,
        text_preview=text_preview,
        merge_key=merge_key,
    )
    if _should_copy_to_tts_scope(event, scope):
        _GLOBAL_GUI_LOG_BUFFER.append(
            timestamp=timestamp,
            scope=GUI_LOG_SCOPE_TTS,
            level=level,
            category=category_text,
            message=message_text,
            detail=detail,
            text_preview=text_preview,
            merge_key=merge_key,
        )
    return record


def _should_copy_to_tts_scope(event: Any, scope: str) -> bool:
    channel = str(getattr(event, "channel", "")).lower()
    if channel != "tts" or scope == GUI_LOG_SCOPE_TTS:
        return False
    return True


def _scope_for_event(event: Any) -> str:
    event_name = str(getattr(event, "event", ""))
    channel = str(getattr(event, "channel", "")).lower()
    if channel == "tts" and event_name.startswith("tts.service."):
        return GUI_LOG_SCOPE_TTS
    return GUI_LOG_SCOPE_PROGRAM


def _category_label(channel: str) -> str:
    normalized = channel.strip().lower()
    labels = {
        "api": "API",
        "agent": "Agent",
        "tool": "工具",
        "tts": "TTS",
        "mcp": "MCP",
        "plugin": "插件",
        "app": "应用",
        "config": "配置",
        "storage": "存储",
        "ui": "UI",
    }
    return labels.get(normalized, normalized or "runtime")


def _level_from_event(event: Any) -> str:
    severity = str(getattr(event, "severity", "")).lower()
    if severity == "error":
        return GUI_LOG_LEVEL_ERROR
    if severity in {"warning", "warn"}:
        return GUI_LOG_LEVEL_WARNING
    return GUI_LOG_LEVEL_INFO


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _tts_text_preview(category: str, data: Any | None) -> str:
    """TTS 合成/播放记录提取文本内容预览，供界面灰字直接展示。

    仅 GUI 内存日志使用；文件日志与 detail 字段仍按既有规则脱敏。
    """
    if category.lower() != "tts" or not isinstance(data, dict):
        return ""
    text = data.get("text")
    if not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    if len(text) > _TEXT_PREVIEW_MAX_CHARS:
        return f"{text[:_TEXT_PREVIEW_MAX_CHARS]}…"
    return text


def _format_detail(data: Any | None) -> str:
    if data is None:
        return ""
    safe = _sanitize_detail(data, private_context=False)
    return json.dumps(safe, ensure_ascii=False, default=str)


def _sanitize_detail(value: Any, *, private_context: bool) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        items = list(value.items())
        for key, item_value in items[:_MAX_DETAIL_KEYS]:
            key_text = str(key)
            normalized_key = key_text.lower()
            if _is_sensitive_key(normalized_key):
                sanitized[key_text] = "<redacted>"
                continue
            next_private_context = private_context or _is_private_text_key(normalized_key)
            sanitized[key_text] = _sanitize_detail(
                item_value,
                private_context=next_private_context,
            )
        if len(items) > _MAX_DETAIL_KEYS:
            sanitized["omitted_keys"] = len(items) - _MAX_DETAIL_KEYS
        return sanitized
    if isinstance(value, list):
        if private_context:
            return {"type": "list", "items": len(value)}
        items = [
            _sanitize_detail(item, private_context=private_context)
            for item in value[:_MAX_DETAIL_ITEMS]
        ]
        if len(value) > _MAX_DETAIL_ITEMS:
            items.append({"omitted_items": len(value) - _MAX_DETAIL_ITEMS})
        return items
    if isinstance(value, tuple):
        return _sanitize_detail(list(value), private_context=private_context)
    if isinstance(value, bytes):
        return {"type": "bytes", "bytes": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return {"type": "image_data_url", "chars": len(value)}
        if private_context:
            return {"type": "text", "chars": len(value)}
        return _truncate(value, _MAX_DETAIL_TEXT_CHARS)
    return value


def _is_sensitive_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS)


def _is_private_text_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _PRIVATE_TEXT_KEY_MARKERS)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated {len(text) - max_chars} chars>"
