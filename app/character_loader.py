from __future__ import annotations

from pathlib import Path


FALLBACK_SYSTEM_PROMPT = """你是夜乃桜，一个冷静、克制、可靠的桌宠陪伴人格。
默认用简短日语回复用户，适合 TTS 朗读。用户需要中文解释、开发或调试时，可以使用中文。
不要输出 Markdown 列表、动作旁白或系统解释。"""


def load_system_prompt(path: Path) -> str:
    if not path.exists():
        return FALLBACK_SYSTEM_PROMPT

    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_SYSTEM_PROMPT

    if not content:
        return FALLBACK_SYSTEM_PROMPT

    return (
        content
        + "\n\n"
        + "当前运行环境是桌面宠物聊天窗口。除非用户明确要求解释或调试，回复应简短、自然、适合朗读。"
    )
