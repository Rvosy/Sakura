from __future__ import annotations

from app.core.debug_log import debug_body_enabled, debug_enabled, debug_log, sanitize_debug_data


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
