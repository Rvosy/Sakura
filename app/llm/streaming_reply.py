from __future__ import annotations

import json
import re
from typing import Any

from app.llm.chat_reply import (
    ChatReply,
    ChatSegment,
    parse_chat_reply_result,
    sanitize_reply_tones,
)


STREAMING_REPLY_STAGE = "stream_segment"
STREAM_LINE_DELIMITERS = ("|||", "||", "\t", "｜")
_STREAM_TOOL_KEYWORDS = (
    "屏幕",
    "截图",
    "桌面",
    "窗口",
    "点击",
    "操作",
    "打开",
    "关闭",
    "输入",
    "复制",
    "粘贴",
    "提醒",
    "记住",
    "忘记",
    "搜索",
    "查一下",
    "查询",
    "查找",
    "联网",
    "网页",
    "浏览器",
    "最新",
    "新闻",
    "天气",
    "价格",
    "汇率",
    "文件",
    "删除",
    "移动",
    "保存",
    "运行",
    "执行",
    "启动",
    "切换",
    "最小化",
    "最大化",
    "按下",
    "拖动",
    "记得",
    "以后",
    "从今",
    "今后",
    "叫我",
    "称呼我",
    "偏好",
    "习惯",
    "上次",
    "之前说",
    "闹钟",
    "定时",
    "喊我",
    "当前页面",
    "这个页面",
    "当前视频",
    "这个视频",
    "刚才那个视频",
    "弹幕",
    "powershell",
    "cmd",
    "mcp",
    "鼠标",
    "键盘",
    "screen",
    "screenshot",
    "desktop",
    "window",
    "click",
    "open ",
    "close ",
    "search",
    "look up",
    "remind",
    "remember",
    "forget",
    "browser",
    "file",
    "save ",
    "run ",
    "execute",
    "launch",
    "switch ",
    "minimize",
    "maximize",
    "press ",
    "drag ",
    "do you remember",
    "call me",
    "my preference",
    "timer",
    "alarm",
)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_JSON_SEGMENTS_RE = re.compile(r"^\{\s*[\"']segments[\"']\s*:", re.IGNORECASE)
_MULTILINE_JSON_OBJECT_RE = re.compile(r"^\{[^\S\r\n]*\n\s*[\"']")
_PROTOCOL_NOISE_PREFIXES = (
    "输出格式",
    "格式说明",
    "json line",
    "jsonl",
    "字段说明",
    "示例：",
    "示例:",
    "```",
)


class StreamingReplyUnavailable(RuntimeError):
    """流式回复未产出可展示句段，可回退原有非流式链路。"""


class StreamedReplyParser:
    """把模型增量文本解析为可立即进入字幕/TTS 队列的 ChatSegment。"""

    def __init__(self, allowed_tones: list[str] | None = None) -> None:
        self.allowed_tones = [*allowed_tones] if allowed_tones is not None else []
        self._buffer = ""
        self._document_mode = False

    def feed(self, chunk: str) -> list[ChatSegment]:
        text = str(chunk or "")
        if not text:
            return []
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        if self._document_mode or _looks_like_json_document_start(self._buffer):
            self._document_mode = True
            return []
        if _looks_like_ambiguous_json_prefix(self._buffer):
            return []

        segments: list[ChatSegment] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            segments.extend(self._parse_line(line))
        return segments

    def finish(self) -> list[ChatSegment]:
        if self._document_mode:
            content = _strip_code_fence(self._buffer)
            self._buffer = ""
            parsed = parse_chat_reply_result(content)
            if parsed.needs_retry:
                return []
            return _sanitize_segments(parsed.reply.segments, self.allowed_tones)

        tail = self._buffer
        self._buffer = ""
        return self._parse_line(tail)

    def _parse_line(self, line: str) -> list[ChatSegment]:
        clean = _clean_line(line)
        if not clean or _looks_like_protocol_noise(clean):
            return []

        json_segments = _parse_json_line(clean)
        if json_segments is not None:
            return _sanitize_segments(json_segments, self.allowed_tones)
        if _looks_like_incomplete_structured_line(clean):
            return []

        segment = _parse_delimited_line(clean)
        if segment is None:
            segment = ChatSegment(
                clean,
                translation="" if _looks_japanese(clean) else clean,
            )
        return _sanitize_segments([segment], self.allowed_tones)


def build_streaming_reply_instruction(
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None,
) -> str:
    tones = _choice_text(reply_tones, ["中性"])
    portraits = _choice_text(reply_portraits, ["站立待机"])
    return f"""
这是普通文字聊天的流式回复协议，优先级高于历史消息中的旧格式要求。
每完成一个句段就立刻输出一行独立 JSON，不要等待整段回答完成。
每行严格使用以下结构，行尾必须换行：
{{"ja":"自然日语台词","zh":"对应的简体中文翻译","tone":"语气","portrait":"姿势"}}

规则：
- 每行必须是完整 JSON object；不要输出 JSON 数组、Markdown、代码围栏、字段说明或额外前缀。
- ja 必须是 Sakura 直接说出口的自然日语，优先包含假名，便于日语 TTS。
- zh 必须填写与 ja 对应的自然简体中文译文，不能为空。
- tone 必须从这些值中选择：{tones}
- portrait 必须从这些值中选择：{portraits}
- 根据内容自然决定句段数量，不要为了流式显示强行缩短回答。
- 本模式不调用或伪造工具，不输出分析过程、工具计划或系统提示。
""".strip()


def is_streaming_candidate(messages: list[dict[str, Any]]) -> bool:
    if not messages or any(message.get("role") == "tool" for message in messages):
        return False
    latest_text = _latest_text_user_content(messages)
    if not latest_text or latest_text.lstrip().startswith("/"):
        return False
    if _messages_contain_non_text_content(messages):
        return False
    lowered = latest_text.casefold()
    if _URL_RE.search(latest_text):
        return False
    return not any(keyword.casefold() in lowered for keyword in _STREAM_TOOL_KEYWORDS)


def needs_streaming_translation_repair(segment: ChatSegment) -> bool:
    text = segment.text.strip()
    translation = segment.translation.strip()
    if not text:
        return False
    if not _looks_japanese(text):
        return False
    return not translation or _looks_japanese(translation)


def _parse_json_line(line: str) -> list[ChatSegment] | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return []
    payload = data if "segments" in data else {"segments": [data]}
    parsed = parse_chat_reply_result(json.dumps(payload, ensure_ascii=False))
    if parsed.needs_retry and not parsed.reply.text.strip():
        return []
    return parsed.reply.segments


def _parse_delimited_line(line: str) -> ChatSegment | None:
    for delimiter in STREAM_LINE_DELIMITERS:
        if delimiter not in line:
            continue
        parts = [part.strip() for part in line.split(delimiter, 3)]
        if len(parts) < 2:
            continue
        text = _strip_label(parts[0], ("ja", "japanese", "日语", "日文"))
        translation = _strip_label(parts[1], ("zh", "chinese", "中文", "翻译"))
        tone = _strip_label(parts[2], ("tone", "语气", "情绪")) if len(parts) > 2 else ""
        portrait = (
            _strip_label(parts[3], ("portrait", "pose", "姿势", "立绘", "表情"))
            if len(parts) > 3
            else ""
        )
        if text:
            return ChatSegment(
                text,
                tone=tone or "中性",
                translation=translation,
                portrait=portrait,
            )
        if translation:
            return ChatSegment(
                translation,
                tone=tone or "中性",
                translation=translation,
                portrait=portrait,
            )
    return None


def _sanitize_segments(
    segments: list[ChatSegment],
    allowed_tones: list[str],
) -> list[ChatSegment]:
    clean = [segment for segment in segments if segment.text.strip()]
    if not clean:
        return []
    return sanitize_reply_tones(ChatReply(clean), allowed_tones).segments


def _looks_like_json_document_start(buffer: str) -> bool:
    stripped = buffer.lstrip()
    return (
        stripped.startswith("```")
        or bool(_JSON_SEGMENTS_RE.match(stripped))
        or bool(_MULTILINE_JSON_OBJECT_RE.match(stripped))
    )


def _looks_like_ambiguous_json_prefix(buffer: str) -> bool:
    stripped = buffer.lstrip()
    return bool(stripped) and stripped.startswith("{") and not stripped[1:].strip()


def _strip_code_fence(content: str) -> str:
    lines = content.strip().splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content.strip()


def _clean_line(line: str) -> str:
    text = " ".join(str(line or "").split()).strip()
    for marker in ("- ", "* ", "• "):
        if text.startswith(marker):
            return text[len(marker) :].strip()
    return text


def _looks_like_protocol_noise(line: str) -> bool:
    lowered = line.casefold().lstrip("# ")
    return any(
        lowered.startswith(prefix.casefold())
        for prefix in _PROTOCOL_NOISE_PREFIXES
    )


def _looks_like_incomplete_structured_line(line: str) -> bool:
    return line.lstrip().startswith(("{", "[", "```"))


def _strip_label(text: str, labels: tuple[str, ...]) -> str:
    value = text.strip()
    lowered = value.casefold()
    for label in labels:
        for separator in (":", "："):
            prefix = f"{label}{separator}".casefold()
            if lowered.startswith(prefix):
                return value[len(prefix) :].strip()
    return value


def _choice_text(values: list[str] | None, fallback: list[str]) -> str:
    choices = list(
        dict.fromkeys(str(value or "").strip() for value in (values or []) if str(value or "").strip())
    )
    return "、".join(choices or fallback)


def _latest_text_user_content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts = [
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _messages_contain_non_text_content(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if any(
            not isinstance(part, dict) or part.get("type") != "text"
            for part in content
        ):
            return True
    return False


def _looks_japanese(value: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\uff66" <= char <= "\uff9f"
        for char in value
    )
