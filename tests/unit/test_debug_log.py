from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.core.debug_log import (
    _close_file_logger_for_tests,
    debug_body_enabled,
    debug_enabled,
    debug_log,
    sanitize_debug_data,
)


@pytest.fixture(autouse=True)
def close_file_logger_after_test():  # type: ignore[no-untyped-def]
    yield
    _close_file_logger_for_tests()


def test_debug_log_disabled_by_default(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.debug_log._load_debug_values", lambda: {})

    debug_log("Test", "不会输出", {"content": "正文"})

    assert capsys.readouterr().out == ""


def test_debug_log_outputs_summary_when_enabled(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.core.debug_log._load_debug_values", lambda: {"enabled": True})

    debug_log("API", "请求开始", {"model": "demo", "content": "你好"})

    output = capsys.readouterr().out
    assert "[Debug][API][" in output
    assert "请求开始" in output
    assert '"chars": 2' in output


def test_file_log_writes_when_terminal_log_disabled(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("file_disabled_terminal")
    monkeypatch.setattr("app.core.debug_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr(
        "app.core.debug_log._load_debug_values",
        lambda: {"enabled": False, "body_enabled": True, "file_enabled": True},
    )

    debug_log(
        "API",
        "准备发送聊天补全请求",
        {
            "api_key": "sk-secret",
            "system_prompt": "绝对不能写入文件的模型提示词",
            "messages": [{"role": "user", "content": "用户聊天正文"}],
            "content": "完整模型回复正文",
        },
    )

    assert capsys.readouterr().out == ""
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    encoded = json.dumps(record, ensure_ascii=False)

    assert record["category"] == "API"
    assert record["message"] == "准备发送聊天补全请求"
    assert "<redacted>" in encoded
    assert "绝对不能写入文件的模型提示词" not in encoded
    assert "用户聊天正文" not in encoded
    assert "完整模型回复正文" not in encoded
    assert '"chars"' in encoded
    assert '"preview"' not in encoded
    _close_file_logger_for_tests()


def test_file_log_ignores_body_enabled_full_text(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("file_body_guard")
    monkeypatch.setattr("app.core.debug_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr(
        "app.core.debug_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": True, "file_enabled": True},
    )

    debug_log("API", "模型原始文本返回", {"content": "终端允许但文件不能写的完整正文"})

    assert "终端允许但文件不能写的完整正文" in capsys.readouterr().out
    record_text = log_path.read_text(encoding="utf-8")
    assert "终端允许但文件不能写的完整正文" not in record_text
    assert '"chars"' in record_text
    assert '"preview"' not in record_text
    _close_file_logger_for_tests()


def test_file_log_rotates_by_size(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    log_path = _runtime_log_path("file_rotate")
    monkeypatch.setattr("app.core.debug_log._FILE_LOG_PATH", log_path)
    monkeypatch.setattr("app.core.debug_log.FILE_LOG_MAX_BYTES", 220)
    monkeypatch.setattr("app.core.debug_log.FILE_LOG_BACKUP_COUNT", 2)
    monkeypatch.setattr(
        "app.core.debug_log._load_debug_values",
        lambda: {"enabled": False, "file_enabled": True},
    )

    for index in range(12):
        debug_log("Rotate", "写入滚动日志", {"index": index, "value": "x" * 120})

    files = sorted(path.name for path in log_path.parent.glob("sakura-runtime.log*"))
    assert "sakura-runtime.log" in files
    assert "sakura-runtime.log.1" in files
    assert len(files) <= 3
    _close_file_logger_for_tests()


def test_debug_body_disabled_keeps_only_body_summary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.debug_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": False},
    )

    content = "开头" + "中间" * 120 + "隐藏末尾"
    data = sanitize_debug_data({"content": content})

    assert data["content"]["chars"] == len(content)
    assert "隐藏末尾" not in data["content"]["preview"]


def test_debug_body_enabled_allows_full_short_body(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.debug_log._load_debug_values",
        lambda: {"enabled": True, "body_enabled": True},
    )

    assert debug_enabled()
    assert debug_body_enabled()
    assert sanitize_debug_data({"content": "完整正文"})["content"] == "完整正文"


def test_debug_data_redacts_sensitive_keys_and_summarizes_images() -> None:
    data = sanitize_debug_data(
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


def test_debug_data_truncates_long_values() -> None:
    data = sanitize_debug_data({"value": "x" * 800}, include_body=True)

    assert len(data["value"]) < 700
    assert "<truncated" in data["value"]


def _runtime_log_path(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "temp" / "test_runtime" / uuid.uuid4().hex / name
    root.mkdir(parents=True, exist_ok=True)
    return root / "sakura-runtime.log"
