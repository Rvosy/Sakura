from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


DEFAULT_TONE = "中性"


@dataclass(frozen=True)
class ChatSegment:
    text: str
    tone: str = DEFAULT_TONE


@dataclass(frozen=True)
class ChatReply:
    segments: list[ChatSegment]

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments).strip()

    @property
    def tone(self) -> str:
        for segment in self.segments:
            if segment.text.strip() and segment.tone.strip():
                return segment.tone.strip()
        return DEFAULT_TONE


def parse_chat_reply(content: str) -> ChatReply:
    """解析模型返回；非 JSON 或旧格式会自动降级成单段中性回复。"""
    content = content.strip()
    if not content:
        return ChatReply([ChatSegment("", DEFAULT_TONE)])

    data = _try_load_json(content)
    if data is None:
        return ChatReply([ChatSegment(content, DEFAULT_TONE)])

    if isinstance(data, dict):
        segments = _parse_segments(data)
        if segments:
            return ChatReply(segments)

    return ChatReply([ChatSegment(content, DEFAULT_TONE)])


def _parse_segments(data: dict[str, Any]) -> list[ChatSegment]:
    raw_segments = data.get("segments")
    if isinstance(raw_segments, list):
        segments = [_parse_segment(item) for item in raw_segments]
        return [segment for segment in segments if segment is not None]

    reply = data.get("reply")
    if isinstance(reply, str) and reply.strip():
        tone = data.get("tone")
        return [ChatSegment(reply.strip(), _clean_tone(tone))]

    text = data.get("text")
    if isinstance(text, str) and text.strip():
        tone = data.get("tone")
        return [ChatSegment(text.strip(), _clean_tone(tone))]

    return []


def _parse_segment(item: Any) -> ChatSegment | None:
    if isinstance(item, str):
        text = item.strip()
        return ChatSegment(text, DEFAULT_TONE) if text else None
    if not isinstance(item, dict):
        return None

    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return ChatSegment(text.strip(), _clean_tone(item.get("tone")))


def _clean_tone(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_TONE


def _try_load_json(content: str) -> Any | None:
    try:
        return json.loads(_strip_code_fence(content))
    except json.JSONDecodeError:
        return None


def _strip_code_fence(content: str) -> str:
    lines = content.strip().splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content
