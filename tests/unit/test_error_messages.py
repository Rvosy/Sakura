from app.ui.error_messages import format_failure_message


def test_format_failure_message_preserves_diagnostic_verbatim() -> None:
    diagnostic = "API HTTP 401: unauthorized\nrequest-id: req-123"

    message = format_failure_message("API 请求失败。", "检查 API 配置。", diagnostic)

    assert message == (
        "发生了什么：API 请求失败。\n\n"
        "处理建议：检查 API 配置。\n\n"
        "诊断信息（截图时请保留）：\n"
        + diagnostic
    )


def test_format_failure_message_omits_empty_diagnostic_section() -> None:
    message = format_failure_message("没有读取到模型。", "检查服务配置。")

    assert message == "发生了什么：没有读取到模型。\n\n处理建议：检查服务配置。"
