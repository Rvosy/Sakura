from __future__ import annotations


def format_failure_message(description: str, action: str, diagnostic: object = "") -> str:
    """生成面向用户的故障说明，并原样保留诊断信息。"""

    message = f"发生了什么：{description}\n\n处理建议：{action}"
    detail = str(diagnostic)
    if detail.strip():
        message += f"\n\n诊断信息（截图时请保留）：\n{detail}"
    return message
