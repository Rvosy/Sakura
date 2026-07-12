from __future__ import annotations

from typing import Any

from app.core.runtime_log import (
    _close_file_logger_for_tests,
    console_log_enabled,
    file_log_enabled,
    format_file_log_data,
    format_log_attributes,
    log_body_enabled,
    log_event,
    sanitize_console_log_data,
    sanitize_file_log_data,
    summarize_messages,
    summarize_text,
)


def debug_enabled() -> bool:
    """兼容旧调试日志 API：返回控制台运行日志是否开启。"""
    return console_log_enabled()


def debug_body_enabled() -> bool:
    """兼容旧调试日志 API：返回 trace 正文输出是否开启。"""
    return log_body_enabled()


def debug_file_enabled() -> bool:
    """兼容旧调试日志 API：返回文件运行日志是否开启。"""
    return file_log_enabled()


def debug_log(category: str, message: str, data: Any | None = None) -> None:
    """兼容旧调试日志 API；实际写入统一运行日志。"""
    log_event(category, message, data)


def format_debug_data(data: Any) -> str:
    """兼容旧调试日志 API；格式化控制台日志属性。"""
    return format_log_attributes(data)


def sanitize_debug_data(data: Any, include_body: bool | None = None) -> Any:
    """兼容旧调试日志 API；脱敏并截断控制台日志属性。"""
    return sanitize_console_log_data(data, include_body=include_body)


__all__ = [
    "_close_file_logger_for_tests",
    "debug_body_enabled",
    "debug_enabled",
    "debug_file_enabled",
    "debug_log",
    "format_debug_data",
    "format_file_log_data",
    "sanitize_debug_data",
    "sanitize_file_log_data",
    "summarize_messages",
    "summarize_text",
]
