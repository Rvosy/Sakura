from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

import app.core.runtime_log as runtime_log_module
from app.core.gui_log import GUI_LOG_SCOPE_PROGRAM, clear_gui_logs, get_gui_log_buffer
from app.core.runtime_log import (
    LogEvent,
    console_log_enabled,
    format_console_event,
    format_log_attributes,
    log_body_enabled,
    log_event,
    sanitize_console_log_data,
)


def test_runtime_log_has_one_persisted_config_source() -> None:
    assert not hasattr(runtime_log_module, "_load_logging_values")
    assert not hasattr(runtime_log_module, "gui_log_enabled")


def test_legacy_debug_log_facades_are_removed() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "app/core/debug_log.py").exists()
    assert not hasattr(runtime_log_module, "raw_tts_service_log_enabled")
    assert not hasattr(runtime_log_module, "_close_file_logger_for_tests")


@pytest.fixture(autouse=True)
def close_file_logger_after_test():  # type: ignore[no-untyped-def]
    clear_gui_logs()
    yield
    clear_gui_logs()


def test_warn_level_filters_info_noise(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """warn 级别仅显示 warning 及以上；info 级事件（如插件事件总线）被过滤。"""
    log_path = _runtime_log_path("warn_filters_info")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "file_enabled": True, "profile": "warn"},
    )

    log_event("PluginEventBus", "派发事件", {"event": "test"})
    log_event("PluginEventBus", "订阅事件", {"event": "test"})

    assert capsys.readouterr().out == ""
    assert not log_path.exists()
    assert get_gui_log_buffer().snapshot() == []


def test_info_level_filters_high_verbosity_noise(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """info 级别只显示关键日常信息，高 verbosity 调试事件被过滤。"""
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "profile": "info"},
    )

    log_event("PetWindow", "运行时事件", {"event": "test"})

    assert capsys.readouterr().out == ""
    assert get_gui_log_buffer().snapshot() == []


def test_key_events_go_to_console_file_and_gui(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("key_events")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "file_enabled": True, "profile": "info"},
    )

    log_event(
        "API",
        "准备发送聊天补全请求",
        {"model": "demo-model", "messages": [{"role": "user", "content": "你好"}], "tools": []},
    )

    output = capsys.readouterr().out
    assert "[API] 发送模型请求" in output
    assert "发送模型请求 │ model=demo-model" in output
    assert "model=demo-model" in output

    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["channel"] == "api"
    assert record["event"] == "api.request.started"
    assert record["message"] == "发送模型请求"
    assert record["attributes"]["messages"][0]["content"]["chars"] == 2
    assert "preview" not in json.dumps(record, ensure_ascii=False)

    gui_records = get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_PROGRAM)
    assert [item.message for item in gui_records] == ["发送模型请求"]


def test_trace_profile_reenables_trace_events(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "file_enabled": False, "profile": "trace"},
    )

    log_event("PetWindow", "运行时事件", {"event": "test"})

    assert "[UI] 运行时事件" in capsys.readouterr().out


def test_trace_level_suppresses_plugin_eventbus_noise(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("trace_suppresses_plugin_eventbus")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "file_enabled": True, "profile": "trace"},
    )

    log_event("PluginEventBus", "订阅事件", {"event": "test"})
    log_event("PluginEventBus", "派发事件", {"event": "test"})

    assert capsys.readouterr().out == ""
    assert not log_path.exists()
    assert get_gui_log_buffer().snapshot() == []


def test_file_log_enabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("file_enabled_by_default")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {})

    log_event("API", "HTTP 请求成功", {"status": 200})

    assert log_path.exists()


def test_console_log_enabled_by_default_but_respects_explicit_false(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {})
    assert console_log_enabled()

    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {"enabled": False})
    assert not console_log_enabled()


def test_file_log_can_be_disabled_explicitly(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("file_disabled_explicitly")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {"file_enabled": False})

    log_event("API", "HTTP 请求成功", {"status": 200})

    assert not log_path.exists()


def test_file_log_never_writes_full_private_text(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("private_text_guard")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": True, "file_enabled": True, "profile": "trace"},
    )

    log_event(
        "API",
        "模型原始文本返回",
        {
            "api_key": "sk-secret",
            "content": "终端 trace 可以显示但文件不能写的完整正文",
        },
    )

    assert "终端 trace 可以显示" in capsys.readouterr().out
    encoded = log_path.read_text(encoding="utf-8")
    assert "<redacted>" in encoded
    assert "终端 trace 可以显示但文件不能写的完整正文" not in encoded
    assert '"chars"' in encoded
    assert '"preview"' not in encoded


def test_file_log_rotates_by_size(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("file_rotate")
    monkeypatch.setattr("app.core.runtime_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr("app.core.runtime_log.FILE_LOG_MAX_BYTES", 260)
    monkeypatch.setattr("app.core.runtime_log.FILE_LOG_BACKUP_COUNT", 2)
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"file_enabled": True, "profile": "trace"},
    )

    for index in range(12):
        log_event("API", "HTTP 请求成功", {"index": index, "status": 200, "response_body": "x" * 120})

    files = sorted(path.name for path in log_path.parent.glob("sakura-runtime.log*"))
    assert "sakura-runtime.log" in files
    assert "sakura-runtime.log.1" in files
    assert len(files) <= 3


def test_console_sanitizer_redacts_sensitive_keys_and_summarizes_images() -> None:
    data = sanitize_console_log_data(
        {
            "api_key": "sk-secret",
            "Authorization": "Bearer token",
            "screenshot_data_url": "data:image/png;base64,abc123",
        },
        include_body=True,
    )

    assert data["api_key"] == "<redacted>"
    assert data["Authorization"] == "<redacted>"
    assert data["screenshot_data_url"]["type"] == "image_data_url"


def test_console_event_wraps_time_and_channel_in_brackets() -> None:
    record = LogEvent(
        timestamp="2026-07-02T06:08:54+08:00",
        severity="info",
        verbosity=1,
        channel="plugin",
        event="plugin.event",
        message="派发事件",
    )

    assert format_console_event(record) == "[06:08:54] [PLUGIN] 派发事件"


def test_interaction_stage_logs_human_label_and_diagnostic_fields(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "file_enabled": False, "profile": "debug"},
    )

    log_event(
        "Latency",
        "交互阶段",
        {
            "interaction_id": "interaction-1",
            "stage": "request_messages_ready",
            "stage_label": "请求上下文已准备",
            "elapsed_ms": 245,
            "delta_ms": 112,
            "request_message_count": 4,
            "text": "这段正文不应默认进控制台",
        },
        event="agent.interaction.stage",
    )

    output = capsys.readouterr().out
    assert "[AGENT] 交互阶段：请求上下文已准备" in output
    assert "交互阶段：请求上下文已准备 │ stage=request_messages_ready" in output
    assert "stage=request_messages_ready" in output
    assert "delta_ms=112ms" in output
    assert "stage_label=" not in output
    assert "这段正文不应默认进控制台" not in output


def test_log_body_enabled_works_at_info_level(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": True, "profile": "info"},
    )
    assert log_body_enabled()
    assert sanitize_console_log_data({"content": "完整正文"})["content"] == "完整正文"


def test_console_body_only_prints_raw_model_response(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": True, "file_enabled": False, "profile": "info"},
    )
    log_event(
        "API",
        "准备发送聊天补全请求",
        {
            "model": "demo-model",
            "message_count": 2,
            "payload": {
                "model": "demo-model",
                "messages": [{"role": "user", "content": "不应输出的完整请求"}],
            },
        },
    )
    raw_response = """{
    "operations": [
        {
            "op": "update",
            "id": "499bec3a-630f-4fe2-b722-28618d746237",
            "layer": "procedural",
            "category": "workflow",
            "importance": 0.7,
            "confidence": 0.9,
            "reason": "更新项目进度",
            "content": "当前正在进行日志输出功能的测试与调试。"
        }
    ]
}"""
    log_event(
        "API",
        "模型原始文本返回",
        {"content": raw_response, "reply_chars": len(raw_response)},
    )
    log_event("TTS", "静音 Provider 跳过播放", {"text": "不应重复输出的 TTS 正文"})

    output = capsys.readouterr().out
    assert "不应输出的完整请求" not in output
    assert "不应重复输出的 TTS 正文" not in output
    assert "[text]" not in output
    assert f"[模型回复]\n{raw_response}" in output
    assert "[模型回复 1]" not in output


def test_console_body_falls_back_to_raw_model_response(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.runtime_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": True, "file_enabled": False, "profile": "info"},
    )

    log_event("API", "模型原始文本返回", {"content": "普通文本回复\n第二行"})

    assert "[模型回复]\n普通文本回复\n第二行" in capsys.readouterr().out


def test_format_log_attributes_includes_safe_values(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {"profile": "info"})
    encoded = format_log_attributes({"model": "demo", "content": "你好"})

    assert '"model": "demo"' in encoded
    assert '"chars": 2' in encoded


def _runtime_log_path(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "temp" / "test_runtime" / uuid.uuid4().hex / name
    root.mkdir(parents=True, exist_ok=True)
    return root / "sakura-runtime.log"
