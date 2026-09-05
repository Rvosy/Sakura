from __future__ import annotations

import re
import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.plugins.sakura_plugin_sdk import prepare_log_payload
from app.storage.paths import StoragePaths


DEBUG_KEY = "SAKURA_DEBUG"
DEBUG_BODY_KEY = "SAKURA_DEBUG_BODY"
RUNTIME_LOG_EXTERNAL_ONLY_KEY = "SAKURA_RUNTIME_LOG_EXTERNAL_ONLY"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_EXTERNAL_SINK_LOCK = threading.RLock()
_EXTERNAL_SINK: Callable[["LogEvent"], object] | None = None


def diagnostic_attributes(
    error: BaseException | object,
    *,
    reason_code: str,
    stage: str,
) -> dict[str, object]:
    """Build bounded diagnostics without persisting a traceback or absolute path.

    ``exception_site`` identifies the innermost Python frame as module/function/line.  It
    is intentionally derived from code metadata instead of ``co_filename`` so a
    user's installation path never enters the Runtime log.  ``failure_id`` is a
    stable signature for grouping repeated reports; it does not include the
    exception message because that may contain conversation or credential data.
    """

    attributes: dict[str, object] = {
        "diagnostic": str(error),
        "error_type": type(error).__name__,
        "reason_code": reason_code,
        "stage": stage,
    }
    if isinstance(error, BaseException):
        cause = _root_exception(error)
        if cause is not error:
            attributes["cause_type"] = type(cause).__name__
        source = _exception_source(cause) or _exception_source(error)
        if source:
            attributes["exception_site"] = source
        signature_site = source.rpartition(":")[0] if source else ""
        signature = "|".join(
            (
                str(reason_code),
                str(stage),
                type(error).__name__,
                type(cause).__name__,
                signature_site,
            )
        )
        attributes["failure_id"] = (
            hashlib.sha256(signature.encode("utf-8")).hexdigest()[:10].upper()
        )
    return attributes


def _root_exception(error: BaseException) -> BaseException:
    current = error
    seen = {id(current)}
    while True:
        candidate = current.__cause__
        if candidate is None and not current.__suppress_context__:
            candidate = current.__context__
        if not isinstance(candidate, BaseException) or id(candidate) in seen:
            return current
        seen.add(id(candidate))
        current = candidate


def _exception_source(error: BaseException) -> str:
    traceback = error.__traceback__
    if traceback is None:
        return ""
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    frame = traceback.tb_frame
    module = str(frame.f_globals.get("__name__", "unknown"))
    function = str(frame.f_code.co_name)
    safe_module = re.sub(r"[^A-Za-z0-9_.-]+", "_", module).strip("_") or "unknown"
    safe_function = re.sub(r"[^A-Za-z0-9_.<>-]+", "_", function).strip("_") or "unknown"
    return f"{safe_module}:{safe_function}:{traceback.tb_lineno}"[:120]


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
_MAX_BODY_SUMMARY_CHARS = 160
_MAX_LIST_ITEMS = 8


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
_SUPPRESSED_MESSAGES = {
    ("plugineventbus", "订阅事件"),
    ("plugineventbus", "派发事件"),
    ("promptinspector", "Prompt 构建完成"),
    ("promptspector", "Prompt 构建完成"),
    ("api", "准备发送聊天补全请求"),
    ("api", "准备发送原生工具聊天补全请求"),
}
_TRACE_MESSAGES: set[tuple[str, str]] = set()
_DEBUG_MESSAGES = {
    ("latency", "交互阶段"),
    ("api", "准备发送聊天补全请求"),
    ("api", "准备发送原生工具聊天补全请求"),
    ("api", "HTTP 请求成功"),
    ("api", "模型原始文本返回"),
    ("api", "原生工具模型返回"),
    ("promptspector", "Prompt 构建完成"),
    ("promptinspector", "Prompt 构建完成"),
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
    ("agentruntime", "工具调用完成"): ("tool.execution.finished", "工具执行完成"),
    ("agentruntime", "准备工具调用"): ("tool.execution.started", "准备执行工具"),
    ("agentruntime", "工具调用等待用户确认"): ("tool.execution.waiting_confirmation", "工具等待确认"),
    ("agentruntime", "请求屏幕观察 follow-up"): ("screen.capture.started", "模型请求观察屏幕"),
    ("agentruntime", "最终回复生成完成"): ("reply.processing.finished", "回复处理完成"),
    ("agentruntime", "最终回复结构异常，准备请求模型修复"): ("reply.processing.repair_started", "回复格式异常，尝试修复"),
    ("agentruntime", "最终回复修复后仍不合格，使用安全兜底"): ("reply.processing.failed", "回复修复失败，使用安全兜底"),
    ("agentruntime", "最终回复结构修复成功"): ("reply.processing.finished", "回复格式修复完成"),
    ("toolregistry", "准备工具执行"): ("tool.execution.started", "准备执行工具"),
    ("toolregistry", "开始执行工具"): ("tool.execution.started", "开始执行工具"),
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
    ("tts", "提交播放请求"): ("tts.synthesis.started", "开始合成语音"),
    ("tts", "提交预生成请求"): ("tts.synthesis.started", "开始预生成语音"),
    ("tts", "预生成音频已就绪"): ("tts.synthesis.finished", "语音预生成完成"),
    ("tts", "播放失败，已继续显示字幕"): ("tts.playback.failed", "语音播放失败，已继续显示字幕"),
    ("tts", "预生成失败，已继续字幕流程"): ("tts.synthesis.failed", "语音预生成失败，已继续字幕流程"),
    ("tts", "开始后台预热 TTS 服务"): ("tts.service.started", "开始预热 TTS 服务"),
    ("tts", "后台预热 TTS 服务完成"): ("tts.service.ready", "TTS 服务预热完成"),
    ("tts", "后台预热 TTS 服务失败"): ("tts.service.failed", "TTS 服务预热失败"),
    ("tts", "后台预热 TTS 服务异常"): ("tts.service.failed", "TTS 服务预热异常"),
    ("petwindow", "开始手动框选截图"): ("screen.capture.started", "开始框选截图"),
    ("petwindow", "手动框选截图启动失败"): ("screen.capture.failed", "截图启动失败"),
    ("petwindow", "手动框选截图已附加到下一条消息"): ("screen.capture.attached", "截图已附加到下一条消息"),
    ("petwindow", "手动框选截图已取消"): ("screen.capture.cancelled", "截图已取消"),
    ("petwindow", "屏幕观察失败"): ("screen.capture.failed", "屏幕观察失败"),
    ("petwindow", "屏幕观察 follow-up 已排队"): ("screen.capture.attached", "屏幕观察已附加到模型请求"),
    ("petwindow", "主动事件屏幕观察 follow-up 已排队"): ("screen.capture.attached", "主动屏幕观察已附加"),
    ("screenawareness", "主动屏幕上下文已缓存"): ("screen.capture.attached", "主动截图已缓存"),
    ("screenawareness", "主动屏幕上下文批次已附加"): ("screen.capture.attached", "截图批次已附加"),
    ("petwindow", "用户消息入队"): ("chat.request.received", "对话请求已接收"),
    ("petwindow", "收到 Agent 回复"): ("reply.display.completed", "回复已送达界面"),
    ("tts", "已启动本地 GPT-SoVITS 服务"): ("tts.service.started", "已启动 GPT-SoVITS 服务"),
    ("tts", "本地 GPT-SoVITS 服务启动并探测成功"): ("tts.service.ready", "GPT-SoVITS 服务已就绪"),
    ("tts", "已启动本地 Genie TTS 服务"): ("tts.service.started", "已启动 Genie TTS 服务"),
    ("tts", "本地 Genie TTS 服务启动并探测成功"): ("tts.service.ready", "Genie TTS 服务已就绪"),
    ("tts", "服务探测成功"): ("tts.service.ready", "TTS 服务探测成功"),
    ("tts", "Genie 服务探测成功"): ("tts.service.ready", "Genie TTS 服务探测成功"),
    ("tts", "Genie API 端点探测失败"): ("tts.service.probe.failed", "Genie TTS 服务探测失败"),
    ("tts", "Genie API 端点探测返回非 JSON"): ("tts.service.probe.failed", "Genie TTS 服务探测响应无效"),
    ("tts", "角色权重切换完成"): ("tts.weights.ready", "TTS 角色权重切换完成"),
    ("startup", "初始主窗口服务已创建"): ("startup.window_services.created", "初始主窗口服务已创建"),
    ("startup", "后台启动服务已创建"): ("startup.background_services.created", "后台启动服务已创建"),
    ("startup", "后台启动服务已注入窗口"): ("startup.background_services.injected", "后台启动服务已注入窗口"),
    ("pluginmanager", "插件已加载"): ("plugin.loaded", "插件已加载"),
    ("mcp", "服务器工具注册完成"): ("mcp.server.ready", "MCP 服务器工具注册完成"),
    ("mcp", "MCP 工具注册完成"): ("mcp.ready", "MCP 工具注册完成"),
    ("mcp", "MCP 配置未启用"): ("mcp.config.disabled", "MCP 未启用"),
    ("mcp", "连接服务器并读取工具"): ("mcp.server.connecting", "正在连接 MCP 服务器"),
    ("mcp", "连接或读取工具失败，已跳过"): ("mcp.server.failed", "MCP 服务器连接失败，已跳过"),
    ("mcp", "工具名冲突，已跳过"): ("mcp.tool.skipped", "MCP 工具名冲突，已跳过"),
    ("mcp", "配置读取失败，已跳过 MCP"): ("mcp.config.failed", "MCP 配置读取失败，已跳过"),
    ("mcp", "没有注册任何 MCP 工具"): ("mcp.ready", "没有可用的 MCP 工具"),
    ("mcp", "工具调用失败"): ("mcp.tool.failed", "MCP 工具调用失败"),
    ("mcp", "关闭连接失败"): ("mcp.close.failed", "MCP 连接关闭失败"),
    ("mcp", "MCP 连接清理超过总时限"): ("mcp.close.timeout", "MCP 连接清理超时"),
    ("context", "Prompt 依赖已就绪"): ("context.dependencies.ready", "Prompt 依赖已就绪"),
    ("context", "Prompt 依赖未就绪，继续降级对话"): ("context.dependencies.degraded", "Prompt 依赖未就绪，继续降级对话"),
    ("memory", "开始后台记忆整理"): ("memory.curation.started", "开始后台记忆整理"),
    ("memory", "后台记忆整理完成"): ("memory.curation.finished", "后台记忆整理完成"),
    ("memory", "后台记忆整理失败，稍后将重试"): ("memory.curation.failed", "后台记忆整理失败，稍后将重试"),
}
_CHANNEL_ALIASES = {
    "api": "api",
    "agentruntime": "agent",
    "chat": "chat",
    "chatworker": "agent",
    "latency": "agent",
    "context": "context",
    "memory": "memory",
    "screen": "screen",
    "screenawareness": "screen",
    "reply": "reply",
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
    event_is_fixed: bool = False
    plugin_id: str | None = None
    plugin_name: str | None = None
    custom: bool = False


def register_external_sink(sink: Callable[[LogEvent], object]) -> None:
    """Route Core events to the process-owned Runtime v2 sink."""

    if not callable(sink):
        raise TypeError("runtime log sink must be callable")
    global _EXTERNAL_SINK
    with _EXTERNAL_SINK_LOCK:
        if _EXTERNAL_SINK is not None and _EXTERNAL_SINK is not sink:
            raise RuntimeError("runtime log sink is already registered")
        _EXTERNAL_SINK = sink


def unregister_external_sink(sink: Callable[[LogEvent], object]) -> None:
    """Remove a sink only when the caller still owns the registration."""

    global _EXTERNAL_SINK
    with _EXTERNAL_SINK_LOCK:
        if _EXTERNAL_SINK is sink:
            _EXTERNAL_SINK = None


def _registered_external_sink() -> Callable[[LogEvent], object] | None:
    with _EXTERNAL_SINK_LOCK:
        return _EXTERNAL_SINK


def external_runtime_sink_active() -> bool:
    """Return whether Runtime v2 currently owns Core log persistence."""

    return _registered_external_sink() is not None


def submit_external_log_event(record: LogEvent) -> bool:
    """Submit only to the installed Runtime v2 sink."""

    sink = _registered_external_sink()
    if sink is None:
        return False
    try:
        return bool(sink(record))
    except Exception:
        # Forwarded diagnostics must never fall back to direct file writes or
        # affect the caller when the owning Runtime generation is stopping.
        return False


def console_log_enabled() -> bool:
    """判断是否开启终端运行日志。"""
    return _bool_value(_load_debug_values().get("enabled"), True)


def log_body_enabled() -> bool:
    """判断终端日志是否允许输出完整请求与回复正文。"""
    return console_log_enabled() and _read_bool(DEBUG_BODY_KEY, default=False)


def log_event(
    channel: str,
    message: str,
    attributes: Any | None = None,
    *,
    event: str | None = None,
    severity: str | None = None,
    plugin_id: str | None = None,
    plugin_name: str | None = None,
    verbosity: int | None = None,
) -> None:
    """将结构化运行事件提交给宿主的统一日志服务。

    当前调用链存在交互 ID 时自动附加 interaction_id 字段，
    使一次交互的全链路日志（模型/工具/TTS/存储）可按 ID 串联。
    """
    external_sink = _registered_external_sink()
    if external_sink is None:
        return
    channel_key = _channel_key(channel)
    if (channel_key, str(message)) in _SUPPRESSED_MESSAGES:
        return
    attributes = _attach_interaction_id(attributes)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    normalized_channel = _normalize_channel(channel_key)
    event_is_fixed = bool(str(event or "").strip()) or (
        channel_key,
        str(message),
    ) in _KEY_EVENT_MESSAGES
    event_name, display_message = _resolve_event(channel_key, normalized_channel, message, event)
    attributes = _with_body_free_metrics(event_name, attributes)
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
        event_is_fixed=event_is_fixed,
        plugin_id=plugin_id,
        plugin_name=plugin_name,
    )

    try:
        external_sink(record)
    except Exception:
        # Logging must never affect Core work.
        pass


@contextmanager
def suppress_runtime_logs():
    """Compatibility scope: host logging has no fallback outputs to suppress."""
    yield


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


def _with_body_free_metrics(event_name: str, attributes: Any | None) -> Any:
    if not isinstance(attributes, dict):
        return attributes
    output = attributes
    if event_name.startswith(("tts.request.", "tts.synthesis.")):
        text = attributes.get("text")
        if isinstance(text, str) and "text_chars" not in attributes:
            output = {**attributes, "text_chars": len(text)}
    return output


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
        value = attributes.get("trace_id")
        if value:
            return str(value)
    return ""


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


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated {len(text) - max_chars} chars>"


def _read_bool(key: str, default: bool) -> bool:
    debug_values = _load_debug_values()
    aliases = {
        DEBUG_KEY: "enabled",
        DEBUG_BODY_KEY: "body_enabled",
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


def log_message(
    severity: str, message: str, *, fields: Any = None,
    component: str = "app", plugin_id: str | None = None, plugin_name: str | None = None,
) -> bool:
    """Submit a custom event to the same host pipeline as fixed runtime events."""
    try:
        message, attributes = prepare_log_payload(message, fields)
        attributes = _attach_interaction_id(attributes)
        return submit_external_log_event(LogEvent(
            timestamp="", severity=severity, verbosity=2, channel=component,
            event="runtime.message", message=message, attributes=attributes,
            plugin_id=plugin_id, plugin_name=plugin_name, custom=True,
        ))
    except Exception:
        return False
