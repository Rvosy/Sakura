from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.core.gui_log import record_debug_log_for_gui
from app.storage.paths import StoragePaths



DEBUG_KEY = "SAKURA_DEBUG"
DEBUG_BODY_KEY = "SAKURA_DEBUG_BODY"
DEBUG_FILE_KEY = "SAKURA_DEBUG_FILE"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SENSITIVE_KEY_MARKERS = ("api_key", "authorization", "token", "secret", "password")
_BODY_KEY_MARKERS = (
    "body",
    "content",
    "messages",
    "prompt",
    "reply",
    "response",
    "system_prompt",
    "text",
)
_FILE_BODY_KEY_MARKERS = (
    *_BODY_KEY_MARKERS,
    "input",
    "output",
    "payload",
    "query",
    "memory",
    "translation",
)
_MAX_TEXT_CHARS = 600
_MAX_BODY_CHARS = 8000
_MAX_BODY_SUMMARY_CHARS = 160
_MAX_LIST_ITEMS = 8
_MAX_DICT_ITEMS = 24
FILE_LOG_MAX_BYTES = 10 * 1024 * 1024
FILE_LOG_BACKUP_COUNT = 5
_FILE_LOG_PATH = StoragePaths(Path(__file__).resolve().parents[2]).runtime_log_file()
_FILE_LOGGER_NAME = "sakura.runtime_file_log"
_file_logger_signature: tuple[str, int, int] | None = None


def debug_enabled() -> bool:
    """判断是否开启终端调试日志。"""
    return _read_bool(DEBUG_KEY, default=False)


def debug_body_enabled() -> bool:
    """判断调试日志是否允许输出完整正文。"""
    return debug_enabled() and _read_bool(DEBUG_BODY_KEY, default=False)


def debug_file_enabled() -> bool:
    """判断是否开启文件运行日志。"""
    return _read_bool(DEBUG_FILE_KEY, default=False)


def debug_log(category: str, message: str, data: Any | None = None) -> None:
    """按统一格式输出调试日志；文件日志始终使用严格脱敏数据。

    当前调用链存在交互 ID 时自动附加 interaction_id 字段，
    使一次交互的全链路日志（模型/工具/TTS/存储）可按 ID 串联。
    """
    data = _attach_interaction_id(data)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        record_debug_log_for_gui(category, message, data, timestamp=timestamp)
    except Exception:
        # GUI 日志只是诊断辅助，任何异常都不应影响主流程。
        pass

    terminal_enabled = debug_enabled()
    file_enabled = debug_file_enabled()
    if not terminal_enabled and not file_enabled:
        return

    if terminal_enabled:
        line = f"[Debug][{category}][{timestamp}] {message}"
        if data is not None:
            line = f"{line} {format_debug_data(data)}"
        print(line)
    if file_enabled:
        _write_file_log(timestamp, category, message, data)


def _attach_interaction_id(data: Any) -> Any:
    """data 为 dict 或 None 时附加当前 interaction_id；调用方已显式给出则不覆盖。"""
    try:
        from app.core.interaction import get_interaction_id

        interaction_id = get_interaction_id()
    except Exception:
        return data
    if not interaction_id:
        return data
    if data is None:
        return {"interaction_id": interaction_id}
    if isinstance(data, dict) and "interaction_id" not in data:
        return {"interaction_id": interaction_id, **data}
    return data


def format_debug_data(data: Any) -> str:
    """格式化调试数据，供测试和日志输出复用。"""
    safe_data = sanitize_debug_data(data, include_body=debug_body_enabled())
    return json.dumps(safe_data, ensure_ascii=False, default=str)


def format_file_log_data(data: Any) -> str:
    """格式化文件日志数据；不输出正文预览。"""
    safe_data = sanitize_file_log_data(data)
    return json.dumps(safe_data, ensure_ascii=False, default=str)


def sanitize_debug_data(data: Any, include_body: bool | None = None) -> Any:
    """脱敏并截断调试数据。include_body=False 时只保留正文摘要。"""
    if include_body is None:
        include_body = debug_body_enabled()
    return _sanitize_value(data, include_body=include_body, body_context=False)


def sanitize_file_log_data(data: Any) -> Any:
    """脱敏文件日志数据，并彻底移除模型提示词、对话正文和工具结果全文。"""
    return _sanitize_value(data, include_body=False, body_context=False, file_safe=True)


def summarize_text(
    text: str,
    max_chars: int = _MAX_BODY_SUMMARY_CHARS,
    *,
    include_preview: bool = True,
) -> dict[str, Any]:
    """生成正文摘要，避免默认日志泄露完整内容。"""
    summary: dict[str, Any] = {
        "type": "text",
        "chars": len(text),
    }
    if include_preview:
        summary["preview"] = _truncate_text(text, max_chars)
    return summary


def summarize_messages(
    messages: list[dict[str, Any]],
    *,
    include_preview: bool = True,
) -> list[dict[str, Any]]:
    """摘要化 OpenAI 兼容消息列表。"""
    summarized: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        content = message.get("content")
        item: dict[str, Any] = {
            "index": index,
            "role": message.get("role", ""),
        }
        if isinstance(content, str):
            item["content"] = summarize_text(content, include_preview=include_preview)
        elif isinstance(content, list):
            item["content"] = [
                _summarize_content_part(part, include_preview=include_preview)
                for part in content[:_MAX_LIST_ITEMS]
            ]
            if len(content) > _MAX_LIST_ITEMS:
                item["omitted_parts"] = len(content) - _MAX_LIST_ITEMS
        else:
            item["content_type"] = type(content).__name__
        summarized.append(item)
    return summarized


def _summarize_content_part(part: Any, *, include_preview: bool = True) -> Any:
    if not isinstance(part, dict):
        return {"type": type(part).__name__}
    part_type = part.get("type")
    if part_type == "text":
        return {
            "type": "text",
            "text": summarize_text(str(part.get("text", "")), include_preview=include_preview),
        }
    if part_type == "image_url":
        return {"type": "image_url", "image_url": "<image omitted>"}
    return {"type": part_type or "unknown", "keys": sorted(str(key) for key in part.keys())}


def _sanitize_value(
    value: Any,
    *,
    include_body: bool,
    body_context: bool,
    file_safe: bool = False,
) -> Any:
    if isinstance(value, dict):
        return _sanitize_dict(
            value,
            include_body=include_body,
            body_context=body_context,
            file_safe=file_safe,
        )
    if isinstance(value, list):
        if file_safe and body_context:
            return _summarize_private_value_for_file(value)
        items = [
            _sanitize_value(
                item,
                include_body=include_body,
                body_context=body_context,
                file_safe=file_safe,
            )
            for item in value[:_MAX_LIST_ITEMS]
        ]
        if len(value) > _MAX_LIST_ITEMS:
            items.append({"omitted_items": len(value) - _MAX_LIST_ITEMS})
        return items
    if isinstance(value, tuple):
        return _sanitize_value(
            list(value),
            include_body=include_body,
            body_context=body_context,
            file_safe=file_safe,
        )
    if isinstance(value, bytes):
        return {"type": "bytes", "bytes": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        if _looks_like_image_data_url(value):
            return {"type": "image_data_url", "chars": len(value)}
        if file_safe and body_context:
            return summarize_text(value, include_preview=False)
        if body_context and not include_body:
            return summarize_text(value)
        if body_context and include_body:
            return _truncate_text(value, _MAX_BODY_CHARS)
        return _truncate_text(value, _MAX_TEXT_CHARS)
    return value


def _sanitize_dict(
    value: dict[Any, Any],
    *,
    include_body: bool,
    body_context: bool,
    file_safe: bool = False,
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    items = list(value.items())
    for key, item_value in items[:_MAX_DICT_ITEMS]:
        key_text = str(key)
        normalized_key = key_text.lower()
        if _is_sensitive_key(normalized_key):
            sanitized[key_text] = "<redacted>"
            continue
        if file_safe:
            file_value = _sanitize_file_dict_item(normalized_key, item_value)
            if file_value is not None:
                sanitized[key_text] = file_value
                continue
        if normalized_key == "messages" and isinstance(item_value, list):
            sanitized[key_text] = (
                _sanitize_value(
                    item_value,
                    include_body=include_body,
                    body_context=False,
                    file_safe=file_safe,
                )
                if include_body
                else summarize_messages([item for item in item_value if isinstance(item, dict)])
            )
            continue
        if normalized_key == "content" and isinstance(item_value, list) and not include_body:
            summarized_parts = [
                _summarize_content_part(part)
                for part in item_value[:_MAX_LIST_ITEMS]
            ]
            if len(item_value) > _MAX_LIST_ITEMS:
                summarized_parts.append({"omitted_items": len(item_value) - _MAX_LIST_ITEMS})
            sanitized[key_text] = summarized_parts
            continue
        next_body_context = body_context or _is_body_key(normalized_key)
        sanitized[key_text] = _sanitize_value(
            item_value,
            include_body=include_body,
            body_context=next_body_context,
            file_safe=file_safe,
        )
    if len(items) > _MAX_DICT_ITEMS:
        sanitized["omitted_keys"] = len(items) - _MAX_DICT_ITEMS
    return sanitized


def _sanitize_file_dict_item(normalized_key: str, value: Any) -> Any | None:
    if normalized_key == "messages" and isinstance(value, list):
        return summarize_messages(
            [item for item in value if isinstance(item, dict)],
            include_preview=False,
        )
    if normalized_key == "payload" and isinstance(value, dict):
        return _summarize_payload_for_file(value)
    if normalized_key == "chat_params" and isinstance(value, dict):
        return _summarize_chat_params_for_file(value)
    if normalized_key == "tools" and isinstance(value, list):
        return _summarize_tools_for_file(value)
    if normalized_key == "tool_calls" and isinstance(value, list):
        return _summarize_tool_calls_for_file(value)
    if normalized_key == "arguments" and isinstance(value, dict):
        return _summarize_dict_shape(value)
    if normalized_key == "arguments_json" and isinstance(value, str):
        return summarize_text(value, include_preview=False)
    if _is_file_body_key(normalized_key):
        return _summarize_private_value_for_file(value)
    return None


def _summarize_payload_for_file(payload: dict[Any, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": "chat_completion_payload",
    }
    for key in (
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "presence_penalty",
        "frequency_penalty",
        "response_format",
        "stream",
        "tool_choice",
    ):
        if key in payload:
            summary[key] = _sanitize_value(
                payload[key],
                include_body=False,
                body_context=False,
                file_safe=True,
            )
    messages = payload.get("messages")
    if isinstance(messages, list):
        message_dicts = [item for item in messages if isinstance(item, dict)]
        summary["message_count"] = len(message_dicts)
        summary["has_image"] = _messages_contain_image_like(message_dicts)
    tools = payload.get("tools")
    if isinstance(tools, list):
        summary["tool_count"] = len(tools)
        summary["tools"] = _summarize_tools_for_file(tools)
    return summary


def _summarize_chat_params_for_file(params: dict[Any, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in params.items():
        key_text = str(key)
        normalized_key = key_text.lower()
        if normalized_key == "tools" and isinstance(value, list):
            summary["tool_count"] = len(value)
            summary["tools"] = _summarize_tools_for_file(value)
            continue
        if _is_sensitive_key(normalized_key):
            summary[key_text] = "<redacted>"
            continue
        if _is_file_body_key(normalized_key):
            summary[key_text] = _summarize_private_value_for_file(value)
            continue
        summary[key_text] = _sanitize_value(
            value,
            include_body=False,
            body_context=False,
            file_safe=True,
        )
    return summary


def _summarize_tools_for_file(tools: list[Any]) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for tool in tools[:_MAX_LIST_ITEMS]:
        item: dict[str, Any] = {"type": "tool"}
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict):
                item["name"] = str(function.get("name", ""))
            elif isinstance(tool.get("name"), str):
                item["name"] = str(tool["name"])
            item["tool_type"] = str(tool.get("type", ""))
        else:
            item["value_type"] = type(tool).__name__
        summarized.append(item)
    if len(tools) > _MAX_LIST_ITEMS:
        summarized.append({"omitted_items": len(tools) - _MAX_LIST_ITEMS})
    return summarized


def _summarize_tool_calls_for_file(tool_calls: list[Any]) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for call in tool_calls[:_MAX_LIST_ITEMS]:
        item: dict[str, Any] = {"type": "tool_call"}
        if isinstance(call, dict):
            function = call.get("function")
            if isinstance(call.get("id"), str):
                item["id"] = call["id"]
            if isinstance(function, dict):
                item["name"] = str(function.get("name", ""))
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    item["argument_keys"] = sorted(str(key) for key in arguments.keys())
                elif isinstance(arguments, str):
                    item["arguments"] = summarize_text(arguments, include_preview=False)
            elif isinstance(call.get("name"), str):
                item["name"] = call["name"]
            if isinstance(call.get("arguments"), dict):
                item["argument_keys"] = sorted(str(key) for key in call["arguments"].keys())
        else:
            item["value_type"] = type(call).__name__
        summarized.append(item)
    if len(tool_calls) > _MAX_LIST_ITEMS:
        summarized.append({"omitted_items": len(tool_calls) - _MAX_LIST_ITEMS})
    return summarized


def _summarize_private_value_for_file(value: Any) -> Any:
    if isinstance(value, str):
        if _looks_like_image_data_url(value):
            return {"type": "image_data_url", "chars": len(value)}
        return summarize_text(value, include_preview=False)
    if isinstance(value, bytes):
        return {"type": "bytes", "bytes": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return {"type": "list", "items": len(value)}
    if isinstance(value, tuple):
        return {"type": "list", "items": len(value)}
    if isinstance(value, dict):
        summary = _summarize_dict_shape(value)
        for key in ("success", "status", "count", "elapsed_ms", "tool_name", "name"):
            if key in value:
                summary[key] = _sanitize_value(
                    value[key],
                    include_body=False,
                    body_context=False,
                    file_safe=True,
                )
        if "error" in value:
            summary["error"] = _sanitize_value(
                value["error"],
                include_body=False,
                body_context=False,
                file_safe=True,
            )
        return summary
    return value


def _summarize_dict_shape(value: dict[Any, Any]) -> dict[str, Any]:
    keys = [str(key) for key in value.keys()]
    summary: dict[str, Any] = {
        "type": "object",
        "keys": sorted(keys[:_MAX_DICT_ITEMS]),
    }
    if len(keys) > _MAX_DICT_ITEMS:
        summary["omitted_keys"] = len(keys) - _MAX_DICT_ITEMS
    return summary


def _messages_contain_image_like(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def _is_sensitive_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS)


def _is_body_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _BODY_KEY_MARKERS)


def _is_file_body_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _FILE_BODY_KEY_MARKERS)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated {len(text) - max_chars} chars>"


def _looks_like_image_data_url(text: str) -> bool:
    return text.startswith("data:image/")


def _read_bool(key: str, default: bool) -> bool:
    debug_values = _load_debug_values()
    aliases = {
        DEBUG_KEY: "enabled",
        DEBUG_BODY_KEY: "body_enabled",
        DEBUG_FILE_KEY: "file_enabled",
    }
    alias = aliases.get(key, key)
    value = debug_values.get(alias, debug_values.get(key))
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def _load_debug_values() -> dict[str, Any]:
    from app.config.yaml_config import load_yaml_mapping
    config_path = StoragePaths(Path(__file__).resolve().parents[2]).system_config()
    try:
        system_config = load_yaml_mapping(config_path)
    except (OSError, ValueError):
        return {}
    debug_config = system_config.get("debug")
    return dict(debug_config) if isinstance(debug_config, dict) else {}


def _write_file_log(
    timestamp: str,
    category: str,
    message: str,
    data: Any | None,
) -> None:
    record: dict[str, Any] = {
        "timestamp": timestamp,
        "category": category,
        "message": message,
    }
    if data is not None:
        record["data"] = sanitize_file_log_data(data)
    try:
        logger = _get_file_logger()
        logger.info(json.dumps(record, ensure_ascii=False, default=str))
    except OSError:
        return


def _get_file_logger() -> logging.Logger:
    global _file_logger_signature

    path = _file_log_path()
    signature = (str(path), FILE_LOG_MAX_BYTES, FILE_LOG_BACKUP_COUNT)
    logger = logging.getLogger(_FILE_LOGGER_NAME)
    if _file_logger_signature == signature and logger.handlers:
        return logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=FILE_LOG_MAX_BYTES,
        backupCount=FILE_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    _file_logger_signature = signature
    return logger


def _file_log_path() -> Path:
    return _FILE_LOG_PATH


def _close_file_logger_for_tests() -> None:
    """关闭文件日志句柄，避免测试临时目录在 Windows 上被占用。"""
    global _file_logger_signature

    logger = logging.getLogger(_FILE_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _file_logger_signature = None
