from __future__ import annotations

import pytest

from app.core.gui_log import (
    GUI_LOG_SCOPE_PROGRAM,
    GUI_LOG_SCOPE_TTS,
    GuiLogBuffer,
    clear_gui_logs,
    get_gui_log_buffer,
)
from app.core.runtime_log import log_event, log_tts_service_output


@pytest.fixture(autouse=True)
def clear_logs_after_test():  # type: ignore[no-untyped-def]
    clear_gui_logs()
    yield
    clear_gui_logs()


def test_gui_log_records_key_events_even_when_console_and_file_disabled(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {})

    log_event("TTS", "发送 GPT-SoVITS 请求", {"api_key": "sk-secret", "text": "不应完整显示的语音文本"})

    assert capsys.readouterr().out == ""
    records = get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_PROGRAM)
    assert records[0].message.startswith("送入 TTS：GPT-SoVITS ")
    assert records[0].text_preview == "不应完整显示的语音文本"
    assert "不应完整显示的语音文本" not in records[0].detail
    tts_records = get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_TTS)
    assert [record.message for record in tts_records] == [records[0].message]


def test_gui_log_suppresses_plugin_eventbus_noise_even_at_trace(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """插件事件总线成功订阅/派发是高频内部噪声，trace 下也不进入 GUI 日志。"""
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {"profile": "trace"})

    log_event("PluginEventBus", "订阅事件", {"event": "test"})
    log_event("PluginEventBus", "派发事件", {"event": "app.started"})

    assert get_gui_log_buffer().snapshot() == []


def test_gui_log_compacts_api_tool_and_tts_timeline() -> None:
    log_event("API", "准备发送原生工具聊天补全请求", {"model": "demo", "messages": [], "tools": []})
    log_event(
        "API",
        "原生工具模型返回",
        {"tool_calls": [{"name": "observe_screen", "arguments": {"reason": "看看屏幕"}}]},
    )
    log_event("ToolRegistry", "工具执行成功", {"tool_name": "observe_screen", "elapsed_ms": 820, "success": True})
    log_event("TTS", "GPT-SoVITS 请求成功", {"provider": "gpt-sovits", "bytes": 2048, "elapsed_ms": 530})

    records = get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_PROGRAM)

    assert [record.message for record in records] == [
        "发送模型请求",
        "收到工具调用：observe_screen",
        "工具执行完成：observe_screen 820ms",
        "TTS 合成完成：GPT-SoVITS 2048B",
    ]
    assert "看看屏幕" not in records[1].detail
    assert [
        record.message for record in get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_TTS)
    ] == ["TTS 合成完成：GPT-SoVITS 2048B"]


def test_gui_log_routes_tts_service_summary_to_tts_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {"profile": "info", "file_enabled": False})
    monkeypatch.setattr("app.core.runtime_log._load_logging_values", lambda: {})

    log_tts_service_output("GPT-SoVITS", "########## 合成音频 ##########")
    log_tts_service_output("GPT-SoVITS", "实际输入的目标文本(切句后): ['そんなの当たり前だっ。']")
    log_tts_service_output("GPT-SoVITS", 'INFO: 127.0.0.1:49840 - "POST /tts HTTP/1.1" 200 OK')
    log_tts_service_output("GPT-SoVITS", 'INFO:     Uvicorn running on http://127.0.0.1:9880 (Press CTRL+C to quit)')

    records = get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_TTS)

    assert [record.message for record in records] == [
        "TTS 服务开始合成音频",
        "TTS 服务收到合成文本",
        "TTS 服务已就绪：http://127.0.0.1:9880",
    ]
    assert all("そんなの" not in record.detail for record in records)
    assert get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_PROGRAM) == []


def test_tts_progress_stays_out_of_runtime_gui_log(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.runtime_log._load_debug_values", lambda: {"profile": "trace"})

    emitted = log_tts_service_output("GPT-SoVITS", " 24%|██▍       | 363/1500 [00:03<00:10, 105.23it/s]")

    assert not emitted
    assert get_gui_log_buffer().snapshot(scope=GUI_LOG_SCOPE_TTS) == []


def test_gui_log_buffer_keeps_scope_limited_ring() -> None:
    buffer = GuiLogBuffer(max_records_per_scope=3)

    for index in range(5):
        buffer.append(
            timestamp="2026-06-11T10:00:00+08:00",
            scope=GUI_LOG_SCOPE_PROGRAM,
            level="info",
            category="Test",
            message=f"item-{index}",
        )

    records = buffer.snapshot(scope=GUI_LOG_SCOPE_PROGRAM)
    assert [record.message for record in records] == ["item-2", "item-3", "item-4"]
