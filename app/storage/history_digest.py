from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.storage.chat_history import ChatHistoryEntry


# 注入跨会话上下文时，最多回看多少条最近对话。
MAX_DIGEST_MESSAGES = 12
MAX_DIGEST_TEXT_CHARS = 220


_VISUAL_MARKER_RE = re.compile(
    r"\[Sakura 已(?:附加手动框选截图|自主观察屏幕)(?:，视觉记录\s+visual_id=[^\]\s]+)?\]"
)
_VISUAL_ID_SUFFIX_RE = re.compile(r"，视觉记录\s+visual_id=[^\]\s]+")
_PUNCT_RE = re.compile(r"[\s，。！？!?、,.~～…]+")
_LOW_SIGNAL_USER_TEXTS = {
    "hi",
    "hello",
    "ok",
    "嗯",
    "哦",
    "好",
    "好的",
    "可以",
    "收到",
    "明白",
    "谢谢",
    "感谢",
    "你好",
    "晚上好",
    "早上好",
    "下午好",
    "是的",
    "对",
}
_PROCESS_ASSISTANT_PATTERNS = (
    "正在确认",
    "我继续推进",
    "稍等",
    "处理中",
    "我先看一下",
    "开始确认",
)


@dataclass(frozen=True)
class DigestLine:
    role: str
    content: str


def clean_recent_dialogue(
    entries: Sequence[ChatHistoryEntry],
    *,
    limit: int = MAX_DIGEST_MESSAGES,
    current_input: str = "",
) -> list[DigestLine]:
    """从聊天历史尾部提取一段清洗后的最近对话，用于跨会话续接。

    只保留 user/assistant 文本，去掉视觉标记等下次会话解析不了的脏数据，
    并丢弃无信息量的应答与纯过程性回复。结果是尽力而为的上下文提示，不要求精确。
    """

    lines: list[DigestLine] = []
    for index, entry in enumerate(entries):
        role = str(entry.role).strip()
        if role not in {"user", "assistant"}:
            continue
        if (
            current_input
            and index == len(entries) - 1
            and role == "user"
            and _truncate(entry.content) == _truncate(current_input)
        ):
            continue
        raw = entry.content
        if role == "assistant" and entry.translation.strip():
            raw = entry.translation
        content = _truncate(raw)
        if not content:
            continue
        if role == "user" and _is_low_signal_user_text(content):
            continue
        if role == "assistant" and _is_process_assistant_text(content):
            continue
        lines.append(DigestLine(role=role, content=content))
    if not lines:
        return []
    return lines[-max(1, limit):]


def _is_low_signal_user_text(text: str) -> bool:
    normalized = _PUNCT_RE.sub("", text).strip().lower()
    if not normalized:
        return True
    if normalized in _LOW_SIGNAL_USER_TEXTS:
        return True
    return len(normalized) <= 1


def _is_process_assistant_text(text: str) -> bool:
    return any(pattern in text for pattern in _PROCESS_ASSISTANT_PATTERNS)


def _strip_visual_markers(text: str) -> str:
    text = _VISUAL_MARKER_RE.sub(" ", text)
    return _VISUAL_ID_SUFFIX_RE.sub(" ", text)


def _normalize_text(text: str) -> str:
    return " ".join(_strip_visual_markers(text).split())


def _truncate(text: str, limit: int = MAX_DIGEST_TEXT_CHARS) -> str:
    text = _normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
