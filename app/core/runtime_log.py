from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.gui_log import record_log_event_for_gui
from app.storage.paths import StoragePaths



DEBUG_KEY = "SAKURA_DEBUG"
DEBUG_BODY_KEY = "SAKURA_DEBUG_BODY"
DEBUG_FILE_KEY = "SAKURA_DEBUG_FILE"
LOG_LEVEL_KEY = "SAKURA_LOG_LEVEL"
RAW_TTS_SERVICE_KEY = "SAKURA_RAW_TTS_SERVICE_LOG"
_TRUE_VALUES = {"1", "true", "yes", "on"}
LOG_LEVEL_ERROR = "error"
LOG_LEVEL_WARN = "warn"
LOG_LEVEL_INFO = "info"
LOG_LEVEL_DEBUG = "debug"
LOG_LEVEL_TRACE = "trace"
LOG_LEVELS = {
    LOG_LEVEL_ERROR,
    LOG_LEVEL_WARN,
    LOG_LEVEL_INFO,
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_TRACE,
}
_LOG_LEVEL_ALIASES = {
    "warning": LOG_LEVEL_WARN,
    "normal": LOG_LEVEL_INFO,
    "verbose": LOG_LEVEL_DEBUG,
    "support": LOG_LEVEL_ERROR,
}
SEVERITY_TRACE = "trace"
SEVERITY_DEBUG = "debug"
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
_SEVERITY_RANK = {
    SEVERITY_TRACE: 0,
    SEVERITY_DEBUG: 1,
    SEVERITY_INFO: 2,
    SEVERITY_WARNING: 3,
    SEVERITY_ERROR: 4,
}
_LEVEL_SEVERITY_THRESHOLD = {
    LOG_LEVEL_ERROR: 4,
    LOG_LEVEL_WARN: 3,
    LOG_LEVEL_INFO: 2,
    LOG_LEVEL_DEBUG: 1,
    LOG_LEVEL_TRACE: 0,
}
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

_ERROR_MARKERS = (
    "error",
    "exception",
    "fail",
    "failed",
    "timeout",
    "不可用",
    "失败",
    "异常",
    "错误",
    "超时",
    "无效",
)
_WARNING_MARKERS = (
    "fallback",
    "warning",
    "回退",
    "警告",
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TTS_HTTP_LINE_RE = re.compile(
    r'"(?P<method>[A-Z]+)\s+(?P<path>[^\s"]+)[^"]*"\s+(?P<status>\d{3})(?:\s+(?P<status_text>[A-Za-z]+))?'
)
_TTS_PROGRESS_RE = re.compile(r"(?P<percent>\d{1,3})%\|")
_TTS_PROGRESS_COUNT_RE = re.compile(r"(?P<current>\d+)\s*/\s*(?P<total>\d+)")
_TTS_PROGRESS_SPEED_RE = re.compile(r"(?P<speed>\d+(?:\.\d+)?)\s*it/s")
_SERVER_PROCESS_RE = re.compile(r"\[(\d+)\]")
_UVICORN_URL_RE = re.compile(r"https?://[^\s)]+")
_SUPPRESSED_MESSAGES = {
    ("plugineventbus", "订阅事件"),
    ("plugineventbus", "派发事件"),
}
_TRACE_MESSAGES: set[tuple[str, str]] = set()
_DEBUG_MESSAGES = {
    ("latency", "交互阶段"),
    ("agentruntime", "准备工具调用"),
    ("agentruntime", "工具调用完成"),
    ("promptspector", "Prompt 构建完成"),
    ("promptinspector", "Prompt 构建完成"),
    ("toolregistry", "准备工具执行"),
    ("toolregistry", "开始执行工具"),
    ("tts", "安排 Qt 多媒体播放器预热"),
    ("tts", "开始预热 Qt 多媒体播放器"),
    ("tts", "Qt 多媒体播放器已初始化"),
}
_LATENCY_STAGE_EVENT = "agent.interaction.stage"
_KEY_EVENT_MESSAGES = {
    ("api", "准备发送聊天补全请求"): ("api.request.started", "发送模型请求"),
    ("api", "准备发送原生工具聊天补全请求"): ("api.request.started", "发送模型请求"),
    ("api", "HTTP 请求成功"): ("api.request.finished", "模型请求成功"),
    ("api", "HTTP 请求失败"): ("api.request.failed", "模型请求失败"),
    ("api", "模型原始文本返回"): ("api.response.received", "收到模型回复"),
    ("api", "原生工具模型返回"): ("api.response.received", "收到模型回复"),
    ("agentruntime", "开始处理用户消息"): ("agent.turn.started", "开始处理用户消息"),
    ("agentruntime", "多步循环完成，返回模型回复"): ("agent.turn.finished", "模型回复已生成"),
    ("toolregistry", "工具等待用户确认"): ("tool.execution.waiting_confirmation", "工具等待确认"),
    ("toolregistry", "工具执行成功"): ("tool.execution.finished", "工具执行完成"),
    ("toolregistry", "工具执行失败"): ("tool.execution.failed", "工具执行失败"),
    ("toolregistry", "工具执行异常"): ("tool.execution.failed", "工具执行异常"),
    ("tts", "发送 GPT-SoVITS 请求"): ("tts.request.started", "送入 TTS：GPT-SoVITS"),
    ("tts", "发送 Genie TTS 请求"): ("tts.request.started", "送入 TTS：Genie"),
    ("tts", "GPT-SoVITS 请求成功"): ("tts.request.finished", "TTS 合成完成：GPT-SoVITS"),
    ("tts", "GPT-SoVITS HTTP 失败"): ("tts.request.failed", "TTS 合成失败：GPT-SoVITS"),
    ("tts", "GPT-SoVITS 请求失败"): ("tts.request.failed", "TTS 合成失败：GPT-SoVITS"),
    ("tts", "GPT-SoVITS 请求超时"): ("tts.request.failed", "TTS 合成失败：GPT-SoVITS"),
    ("tts", "GPT-SoVITS 返回空音频"): ("tts.request.failed", "TTS 合成失败：GPT-SoVITS"),
    ("tts", "Genie 临时音频已写入"): ("tts.request.finished", "TTS 合成完成：Genie"),
    ("tts", "音频请求失败"): ("tts.request.failed", "TTS 合成失败"),
    ("tts", "开始播放音频"): ("tts.playback.started", "开始播放音频"),
    ("tts", "音频播放完成"): ("tts.playback.finished", "音频播放完成"),
    ("tts", "已启动本地 GPT-SoVITS 服务"): ("tts.service.started", "已启动 GPT-SoVITS 服务"),
    ("tts", "本地 GPT-SoVITS 服务启动并探测成功"): ("tts.service.ready", "GPT-SoVITS 服务已就绪"),
    ("tts", "已启动本地 Genie TTS 服务"): ("tts.service.started", "已启动 Genie TTS 服务"),
    ("tts", "本地 Genie TTS 服务启动并探测成功"): ("tts.service.ready", "Genie TTS 服务已就绪"),
    ("tts", "服务探测成功"): ("tts.service.ready", "TTS 服务探测成功"),
    ("tts", "Genie 服务探测成功"): ("tts.service.ready", "Genie TTS 服务探测成功"),
    ("tts", "角色权重切换完成"): ("tts.weights.ready", "TTS 角色权重切换完成"),
    ("startup", "初始主窗口服务已创建"): ("startup.window_services.created", "初始主窗口服务已创建"),
    ("startup", "后台启动服务已创建"): ("startup.background_services.created", "后台启动服务已创建"),
    ("startup", "后台启动服务已注入窗口"): ("startup.background_services.injected", "后台启动服务已注入窗口"),
    ("pluginmanager", "插件已加载"): ("plugin.loaded", "插件已加载"),
    ("mcp", "服务器工具注册完成"): ("mcp.server.ready", "MCP 服务器工具注册完成"),
    ("mcp", "MCP 工具注册完成"): ("mcp.ready", "MCP 工具注册完成"),
}
_CHANNEL_ALIASES = {
    "api": "api",
    "agentruntime": "agent",
    "chatworker": "agent",
    "latency": "agent",
    "toolregistry": "tool",
    "tool": "tool",
    "tts": "tts",
    "mcp": "mcp",
    "plugin": "plugin",
    "pluginmanager": "plugin",
    "plugineventbus": "plugin",
    "startup": "app",
    "crash": "app",
    "config": "config",
    "migration": "config",
    "history": "storage",
    "storage": "storage",
    "ui": "ui",
    "input": "ui",
    "petwindow": "ui",
}


@dataclass(frozen=True)
class LogEvent:
    timestamp: str
    severity: str
    verbosity: int
    channel: str
    event: str
    message: str
    trace_id: str = ""
    attributes: Any | None = None


def console_log_enabled() -> bool:
    """判断是否开启终端运行日志。"""
    values = _load_debug_values()
    logging_values = _load_logging_values()
    value = logging_values.get("console_enabled", values.get("enabled"))
    return _bool_value(value, False)


def file_log_enabled() -> bool:
    """判断是否开启文件运行日志。默认落盘，显式配置可关闭。"""
    values = _load_debug_values()
    logging_values = _load_logging_values()
    value = logging_values.get("file_enabled", values.get("file_enabled"))
    return _bool_value(value, True)


def gui_log_enabled() -> bool:
    logging_values = _load_logging_values()
    return _bool_value(logging_values.get("gui_enabled"), True)


def log_body_enabled() -> bool:
    """判断详细日志是否允许输出完整正文。仅终端 trace 级别调试使用。"""
    return (
        console_log_enabled()
        and log_level() == LOG_LEVEL_TRACE
        and _read_bool(DEBUG_BODY_KEY, default=False)
    )


def raw_tts_service_log_enabled() -> bool:
    """兼容旧调用方；TTS 服务原始 stdout/stderr 现在始终落盘。"""
    return True


def log_level() -> str:
    """返回当前日志级别 (error / warn / info / debug / trace)，默认 info。

    优先从 logging 配置节读取，其次 debug 配置节；同时兼容旧 profile 键名，
    并将旧值 (support/normal/verbose) 归一化为新级别名称。
    """
    logging_values = _load_logging_values()
    debug_values = _load_debug_values()
    # 新键名优先：logging.level > debug.level
    raw = logging_values.get("level")
    if raw is None:
        raw = debug_values.get("level", debug_values.get("profile"))
    value = str(raw or LOG_LEVEL_INFO).strip().lower()
    if value in LOG_LEVELS:
        return value
    return _LOG_LEVEL_ALIASES.get(value, LOG_LEVEL_INFO)


def log_event(
    channel: str,
    message: str,
    attributes: Any | None = None,
    *,
    event: str | None = None,
    severity: str | None = None,
    verbosity: int | None = None,
) -> None:
    """记录一个结构化运行事件，并按统一策略分发到 GUI/控制台/文件。

    当前调用链存在交互 ID 时自动附加 interaction_id 字段，
    使一次交互的全链路日志（模型/工具/TTS/存储）可按 ID 串联。
    """
    channel_key = _channel_key(channel)
    if (channel_key, str(message)) in _SUPPRESSED_MESSAGES:
        return
    attributes = _attach_interaction_id(attributes)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    normalized_channel = _normalize_channel(channel_key)
    event_name, display_message = _resolve_event(channel_key, normalized_channel, message, event)
    display_message = _message_with_attributes(event_name, display_message, attributes)
    resolved_severity = _normalize_severity(
        severity or _infer_severity(message, attributes)
    )
    resolved_verbosity = (
        int(verbosity)
        if verbosity is not None
        else _default_verbosity(channel_key, message, resolved_severity, event_name)
    )
    trace_id = _trace_id_from_attributes(attributes)
    record = LogEvent(
        timestamp=timestamp,
        severity=resolved_severity,
        verbosity=resolved_verbosity,
        channel=normalized_channel,
        event=event_name,
        message=display_message,
        trace_id=trace_id,
        attributes=attributes,
    )

    if gui_log_enabled() and _event_visible(record, sink="gui"):
        try:
            record_log_event_for_gui(record)
        except Exception:
            # GUI 日志只是诊断辅助，任何异常都不应影响主流程。
            pass

    if console_log_enabled() and _event_visible(record, sink="console"):
        print(format_console_event(record))
    if file_log_enabled() and _event_visible(record, sink="file"):
        _write_file_log(record)


def log_tts_service_output(provider: str, line: str) -> bool:
    """把本地 TTS 服务 stdout/stderr 压缩成运行事件。

    返回 True 表示生成了可见或可追踪事件；原始逐行落盘由调用方始终执行。
    """
    raw_line = _normalize_tts_service_line(line)
    if not raw_line:
        return False
    provider_text = str(provider or "TTS").strip() or "TTS"
    attributes: dict[str, Any] = {"provider": provider_text}

    http_match = _TTS_HTTP_LINE_RE.search(raw_line)
    if http_match is not None:
        status = int(http_match.group("status"))
        attributes.update(
            {
                "method": http_match.group("method"),
                "path": http_match.group("path"),
                "status": status,
            }
        )
        ok = 200 <= status < 300
        log_event(
            "TTS",
            f"TTS 服务 HTTP {attributes['method']} {attributes['path']} -> {status}",
            attributes,
            event="tts.service.http",
            severity=SEVERITY_INFO if ok else SEVERITY_WARNING,
            verbosity=5 if ok else 0,
        )
        return True

    if "合成音频" in raw_line:
        log_event(
            "TTS",
            "TTS 服务开始合成音频",
            attributes,
            event="tts.service.synthesis.started",
            verbosity=1,
        )
        return True
    if "实际输入的目标文本" in raw_line or "目标文本" in raw_line:
        attributes["text_chars"] = _service_text_chars(raw_line)
        if attributes["text_chars"] == 0:
            return False
        log_event(
            "TTS",
            "TTS 服务收到合成文本",
            attributes,
            event="tts.service.text.received",
            verbosity=1,
        )
        return True

    progress = _tts_progress_attributes(raw_line)
    if progress is not None:
        return False

    upper = raw_line.upper()
    if upper.startswith("ERROR:"):
        attributes["line"] = raw_line.removeprefix("ERROR:").strip()
        log_event(
            "TTS",
            "TTS 服务输出错误",
            attributes,
            event="tts.service.stderr",
            severity=SEVERITY_ERROR,
            verbosity=0,
        )
        return True
    if upper.startswith("WARNING:"):
        attributes["line"] = raw_line.removeprefix("WARNING:").strip()
        log_event(
            "TTS",
            "TTS 服务输出警告",
            attributes,
            event="tts.service.warning",
            severity=SEVERITY_WARNING,
            verbosity=0,
        )
        return True
    if raw_line.startswith("INFO:"):
        translated = _translate_tts_info_line(raw_line.removeprefix("INFO:").strip())
        if not translated:
            return False
        attributes["line"] = translated
        log_event(
            "TTS",
            translated,
            attributes,
            event="tts.service.info",
            verbosity=1,
        )
        return True
    if any(marker in raw_line.lower() for marker in ("error", "warning", "exception")):
        attributes["line"] = raw_line
        log_event(
            "TTS",
            "TTS 服务输出异常信息",
            attributes,
            event="tts.service.warning",
            severity=SEVERITY_WARNING,
            verbosity=0,
        )
        return True
    return False


def _normalize_tts_service_line(line: str) -> str:
    text = _ANSI_RE.sub("", str(line))
    if "\r" in text:
        segments = [segment for segment in text.split("\r") if segment.strip()]
        text = segments[-1] if segments else ""
    return re.sub(r"\s+", " ", text.strip())


def _service_text_chars(line: str) -> int:
    text = line
    for separator in (":", "："):
        if separator in text:
            text = text.split(separator, 1)[1]
            break
    return len(text.strip(" []'\""))


def _tts_progress_attributes(line: str) -> dict[str, Any] | None:
    percent_match = _TTS_PROGRESS_RE.search(line)
    count_match = _TTS_PROGRESS_COUNT_RE.search(line)
    if percent_match is None and (count_match is None or "it/s" not in line):
        return None
    attributes: dict[str, Any] = {}
    if percent_match is not None:
        attributes["percent"] = min(100, int(percent_match.group("percent")))
    if count_match is not None:
        attributes["current"] = int(count_match.group("current"))
        attributes["total"] = int(count_match.group("total"))
    speed_match = _TTS_PROGRESS_SPEED_RE.search(line)
    if speed_match is not None:
        attributes["speed_it_s"] = float(speed_match.group("speed"))
    return attributes


def _translate_tts_info_line(line: str) -> str:
    lower = line.lower()
    if "started server process" in lower:
        match = _SERVER_PROCESS_RE.search(line)
        pid = match.group(1) if match else ""
        return f"TTS 服务进程已启动 [{pid}]" if pid else "TTS 服务进程已启动"
    if "application startup complete" in lower:
        return "TTS 服务应用启动完成"
    if "uvicorn running on" in lower:
        match = _UVICORN_URL_RE.search(line)
        url = match.group(0) if match else ""
        return f"TTS 服务已就绪：{url}" if url else "TTS 服务已就绪"
    if "application shutdown complete" in lower:
        return "TTS 服务应用已关闭"
    if "finished server process" in lower:
        return "TTS 服务进程已结束"
    return ""


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


def _channel_key(channel: str) -> str:
    return str(channel or "runtime").strip().lower()


def _normalize_channel(channel_key: str) -> str:
    return _CHANNEL_ALIASES.get(channel_key, channel_key or "runtime")


def _resolve_event(
    channel_key: str,
    normalized_channel: str,
    message: str,
    event: str | None,
) -> tuple[str, str]:
    if event:
        return _normalize_event_name(event, normalized_channel, message), str(message)
    rule = _KEY_EVENT_MESSAGES.get((channel_key, str(message)))
    if rule is not None:
        return rule
    return _derive_event_name(normalized_channel, message), str(message)


def _message_with_attributes(event_name: str, message: str, attributes: Any | None) -> str:
    if event_name == _LATENCY_STAGE_EVENT and isinstance(attributes, dict):
        label = attributes.get("stage_label")
        if isinstance(label, str) and label.strip():
            return f"交互阶段：{label.strip()}"
    if event_name == "api.response.received" and isinstance(attributes, dict):
        names = _tool_call_names(attributes.get("tool_calls"))
        if names:
            return f"收到工具调用：{names}"
        if attributes.get("tool_calls") == []:
            return "收到模型回复"
    if event_name == "tool.execution.finished" and isinstance(attributes, dict):
        name = attributes.get("tool_name") or attributes.get("name")
        elapsed = attributes.get("elapsed_ms")
        if name and elapsed is not None:
            return f"工具执行完成：{name} {elapsed}ms"
        if name:
            return f"工具执行完成：{name}"
    if event_name == "tool.execution.failed" and isinstance(attributes, dict):
        name = attributes.get("tool_name") or attributes.get("name")
        if name:
            return f"工具执行失败：{name}"
    if event_name == "tts.request.started" and isinstance(attributes, dict):
        text = attributes.get("text")
        if isinstance(text, str):
            return f"{message} {len(text)}字"
    if event_name == "tts.request.failed" and isinstance(attributes, dict):
        error = attributes.get("error") or attributes.get("message") or attributes.get("reason")
        if error:
            return f"{message}：{error}"
    if event_name == "tts.request.finished" and isinstance(attributes, dict):
        pieces: list[str] = []
        byte_count = attributes.get("bytes") or attributes.get("audio_bytes")
        duration_ms = attributes.get("duration_ms")
        if byte_count is not None:
            pieces.append(f"{byte_count}B")
        if duration_ms is not None:
            pieces.append(f"{duration_ms}ms")
        if pieces:
            return f"{message} {' '.join(pieces)}"
    return message


def _tool_call_names(tool_calls: Any) -> str:
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""
    names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        if not name and isinstance(call.get("function"), dict):
            name = call["function"].get("name")
        if name:
            names.append(str(name))
    if not names:
        return ""
    if len(names) > 3:
        return "、".join(names[:3]) + f" 等 {len(names)} 个"
    return "、".join(names)


def _normalize_event_name(event: str, channel: str, message: str) -> str:
    text = str(event or "").strip().lower()
    if text:
        cleaned = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("._-")
        if cleaned:
            return cleaned
    return _derive_event_name(channel, message)


def _derive_event_name(channel: str, message: str) -> str:
    ascii_text = str(message).encode("ascii", errors="ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if not slug:
        digest = hashlib.sha1(str(message).encode("utf-8")).hexdigest()[:10]
        slug = f"event_{digest}"
    return f"{channel}.{slug[:64]}"


def _infer_severity(message: str, attributes: Any | None) -> str:
    text = str(message).lower()
    if any(marker.lower() in text for marker in _ERROR_MARKERS):
        return SEVERITY_ERROR
    if _data_has_error_value(attributes):
        return SEVERITY_ERROR
    if any(marker.lower() in text for marker in _WARNING_MARKERS):
        return SEVERITY_WARNING
    return SEVERITY_INFO


def _data_has_error_value(data: Any | None) -> bool:
    if not isinstance(data, dict):
        return False
    for key, value in data.items():
        normalized = str(key).lower()
        if normalized not in {"error", "exception", "error_body", "reason", "message"}:
            continue
        if value in (None, "", False, 0):
            continue
        if normalized in {"reason", "message"} and "失败" not in str(value) and "error" not in str(value).lower():
            continue
        return True
    return False


def _normalize_severity(severity: str) -> str:
    value = str(severity or SEVERITY_INFO).strip().lower()
    if value == "warn":
        value = SEVERITY_WARNING
    if value == "fatal":
        value = SEVERITY_ERROR
    return value if value in _SEVERITY_RANK else SEVERITY_INFO


def _default_verbosity(
    channel_key: str,
    message: str,
    severity: str,
    event_name: str,
) -> int:
    if _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK[SEVERITY_WARNING]:
        return 0
    key = (channel_key, str(message))
    if key in _TRACE_MESSAGES:
        return 5
    if key in _DEBUG_MESSAGES:
        return 3
    if event_name == _LATENCY_STAGE_EVENT:
        return 3
    if key in _KEY_EVENT_MESSAGES:
        return 1
    if event_name.startswith(("startup.", "crash.", "api.", "tts.", "tool.", "mcp.", "plugin.")):
        return 1
    if channel_key in {"ui", "input", "petwindow"}:
        return 5
    return 3


def _trace_id_from_attributes(attributes: Any | None) -> str:
    if isinstance(attributes, dict):
        value = attributes.get("interaction_id") or attributes.get("trace_id")
        if value:
            return str(value)
    return ""


def _event_visible(record: LogEvent, *, sink: str) -> bool:
    """根据当前日志级别决定事件是否可见。

    error → 仅严重错误
    warn  → 错误 + 警告
    info  → 关键日常信息（verbosity <= 1）+ 警告/错误
    debug → 详细信息（verbosity <= 3）+ 警告/错误
    trace → 全部结构化软件日志
    """
    _ = sink
    level = log_level()
    rank = _SEVERITY_RANK.get(record.severity, 0)
    threshold = _LEVEL_SEVERITY_THRESHOLD.get(level, 2)
    if rank < threshold:
        return False
    if rank >= _SEVERITY_RANK[SEVERITY_WARNING]:
        return True
    max_verbosity = {
        LOG_LEVEL_INFO: 1,
        LOG_LEVEL_DEBUG: 3,
        LOG_LEVEL_TRACE: 5,
    }.get(level, -1)
    return int(record.verbosity) <= max_verbosity


def format_console_event(record: LogEvent) -> str:
    timestamp = _format_console_timestamp(record.timestamp)
    summary = _format_console_summary(record.attributes)
    line = f"[{timestamp}] [{record.channel.upper()}] {record.message}"
    return f"{line} │ {summary}" if summary else line


def _format_console_timestamp(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
    except ValueError:
        return timestamp


def _format_console_summary(attributes: Any | None) -> str:
    if attributes is None:
        return ""
    include_body = log_body_enabled()
    safe = sanitize_console_log_data(attributes, include_body=include_body)
    if not isinstance(safe, dict):
        return ""
    parts: list[str] = []
    priority = (
        "stage",
        "detail_stage",
        "screen",
        "batch",
        "dropped_count",
        "image_chars",
        "pending_turns",
        "trigger_turns",
        "remaining_turns",
        "screen_name",
        "resolution",
        "model",
        "endpoint_host",
        "provider",
        "tool_name",
        "name",
        "status",
        "elapsed_ms",
        "delta_ms",
        "message_count",
        "request_message_count",
        "tool_count",
        "tool_call_count",
        "sequence_id",
        "segment_count",
        "segments",
        "batch_count",
        "batch_limit",
        "audio_ready",
        "percent",
        "current",
        "total",
        "speed_it_s",
        "bytes",
        "duration_ms",
        "text_chars",
        "reply_chars",
        "error",
        "reason",
    )
    for key in priority:
        if key not in safe:
            continue
        value = safe[key]
        if isinstance(value, (dict, list)):
            continue
        if key in {"elapsed_ms", "delta_ms", "duration_ms"}:
            parts.append(f"{key}={value}ms")
        else:
            parts.append(f"{key}={value}")
        if len(parts) >= 5:
            break
    if include_body:
        body = _first_console_body_value(safe)
        if body:
            parts.append(body)
    return " ".join(parts)


def _first_console_body_value(data: dict[str, Any]) -> str:
    for key in ("content", "text", "reply", "response", "body", "prompt"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return ""


def format_log_attributes(data: Any) -> str:
    """格式化控制台日志属性，供测试和日志输出复用。"""
    safe_data = sanitize_console_log_data(data, include_body=log_body_enabled())
    return json.dumps(safe_data, ensure_ascii=False, default=str)


def format_file_log_data(data: Any) -> str:
    """格式化文件日志数据；不输出正文预览。"""
    safe_data = sanitize_file_log_data(data)
    return json.dumps(safe_data, ensure_ascii=False, default=str)


def sanitize_console_log_data(data: Any, include_body: bool | None = None) -> Any:
    """脱敏并截断控制台日志属性。include_body=False 时只保留正文摘要。"""
    if include_body is None:
        include_body = log_body_enabled()
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


def _bool_value(value: Any, default: bool) -> bool:
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


def _load_logging_values() -> dict[str, Any]:
    from app.config.yaml_config import load_yaml_mapping
    config_path = StoragePaths(Path(__file__).resolve().parents[2]).system_config()
    try:
        system_config = load_yaml_mapping(config_path)
    except (OSError, ValueError):
        return {}
    logging_config = system_config.get("logging")
    return dict(logging_config) if isinstance(logging_config, dict) else {}


def _write_file_log(record_event: LogEvent) -> None:
    record: dict[str, Any] = {
        "timestamp": record_event.timestamp,
        "severity": record_event.severity,
        "verbosity": record_event.verbosity,
        "channel": record_event.channel,
        "event": record_event.event,
        "message": record_event.message,
    }
    if record_event.trace_id:
        record["trace_id"] = record_event.trace_id
    if record_event.attributes is not None:
        record["attributes"] = sanitize_file_log_data(record_event.attributes)
    try:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        path = _file_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_file_log_if_needed(path, len(line.encode("utf-8")))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return


def _rotate_file_log_if_needed(path: Path, pending_bytes: int) -> None:
    if FILE_LOG_MAX_BYTES <= 0 or not path.exists():
        return
    try:
        current_size = path.stat().st_size
    except OSError:
        return
    if current_size + pending_bytes <= FILE_LOG_MAX_BYTES:
        return
    backup_count = max(0, int(FILE_LOG_BACKUP_COUNT))
    if backup_count <= 0:
        path.write_text("", encoding="utf-8")
        return
    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if not source.exists():
            continue
        target = path.with_name(f"{path.name}.{index + 1}")
        target.write_bytes(source.read_bytes())
    path.with_name(f"{path.name}.1").write_bytes(path.read_bytes())
    path.write_text("", encoding="utf-8")


def _file_log_path() -> Path:
    return _FILE_LOG_PATH


def _close_file_logger_for_tests() -> None:
    """兼容测试清理入口；文件日志现在每次写入都会立即关闭。"""
    return
