from __future__ import annotations

from dataclasses import dataclass
from pydantic import ValidationError
from sakura_assistant_contract import ChatReply, ChatSegment
from sakura_structured_output import load_json_object, validation_message
from .reply_schema import ReplyOutput


DEFAULT_TONE = "中性"
SAFE_PARSE_FAILURE_TEXT = "返答の形が少し崩れたみたい。もう一度整理するね。"
SAFE_PARSE_FAILURE_TRANSLATION = "回复格式有点乱，我重新整理一下。"
SAFE_LANGUAGE_FALLBACK_TEXT = "うまく日本語にできなかったみたい。もう一度言い直すね。"


@dataclass(frozen=True)
class ChatReplyParseResult:
    reply: ChatReply
    ok: bool
    needs_retry: bool = False
    repaired: bool = False
    reason: str = ""
    diagnostic: str = ""


@dataclass(frozen=True)
class StructuredJsonParseResult:
    value: dict | None
    raw_status: str
    deterministic_repair: bool = False


def parse_chat_reply(content: str) -> ChatReply:
    """解析模型返回；坏结构化回复会降级成安全提示，避免原文泄到 UI。"""
    return parse_chat_reply_result(content).reply


def parse_chat_reply_result(content: str) -> ChatReplyParseResult:
    """解析模型返回并附带诊断，供 AgentRuntime 决定是否重试。"""
    content = content.strip()
    if not content:
        return ChatReplyParseResult(ChatReply([ChatSegment("", DEFAULT_TONE)]), ok=False, needs_retry=True, reason="empty")

    structured = parse_structured_json(content)
    data, repaired = structured.value, structured.deterministic_repair
    if data is None:
        if _looks_structured_reply(content):
            return ChatReplyParseResult(
                _build_safe_parse_failure_reply(),
                ok=False,
                needs_retry=True,
                reason="invalid_json",
            )
        return ChatReplyParseResult(ChatReply([ChatSegment(content, DEFAULT_TONE)]), ok=True)

    try:
        output = ReplyOutput.model_validate(data)
    except ValidationError as error:
        return ChatReplyParseResult(_build_safe_parse_failure_reply(), ok=False, needs_retry=True,
                                    reason="invalid_structure", diagnostic=validation_message(error))
    parsed = [_build_segment(item.ja, item.tone, item.zh, item.portrait, item.control) for item in output.segments]
    segments = [segment for segment, _issue in parsed]
    has_language_issue = any(issue for _segment, issue in parsed)
    return ChatReplyParseResult(
        ChatReply(segments),
        ok=not has_language_issue,
        needs_retry=has_language_issue,
        repaired=repaired,
        reason="language_issue" if has_language_issue else "",
    )


def sanitize_reply_tones(reply: ChatReply, allowed_tones: list[str] | None) -> ChatReply:
    """把模型偶发越界的 tone（如 en、坚定）归一到 DEFAULT_TONE，避免脏标签流入下游。

    模型被要求只用角色 reply.tones 里的情绪标签，但偶尔会把 tone 字段误当语言码
    或自创类别。这类脏标签在 TTS 侧虽会回退到中性参考，但会污染历史、日志与统计，
    故在产出边界统一清洗。allowed_tones 为空时不处理（保持向后兼容）；只替换 tone，
    不动文本、译文与立绘。
    """
    if not allowed_tones:
        return reply
    allowed = set(allowed_tones)
    changed = False
    new_segments: list[ChatSegment] = []
    for segment in reply.segments:
        if segment.tone and segment.tone not in allowed:
            new_segments.append(
                ChatSegment(
                    segment.text,
                    DEFAULT_TONE,
                    segment.translation,
                    segment.portrait,
                    suppress_tts=segment.suppress_tts,
                    control=segment.control,
                )
            )
            changed = True
        else:
            new_segments.append(segment)
    return ChatReply(new_segments) if changed else reply


def _build_segment(text: str, tone: str, translation: str, portrait: str, control: dict | None = None) -> tuple[ChatSegment, bool]:
    tone = tone or DEFAULT_TONE
    # 只在 ja 明显是中文、zh 明显是日文时交换，避免误判“ 大丈夫 ”这类日语汉字句。
    if text and translation and _looks_chinese(text) and _looks_japanese(translation):
        text, translation = translation, text
        return ChatSegment(text, tone, translation, portrait, control=control), False

    if text and _has_obvious_chinese(text):
        fallback_translation = translation or text
        return (
            ChatSegment(
                SAFE_LANGUAGE_FALLBACK_TEXT,
                tone,
                fallback_translation,
                portrait,
                suppress_tts=True,
                control=control,
            ),
            True,
        )

    return ChatSegment(text, tone, translation, portrait, control=control), False


def _looks_japanese(value: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\uff66" <= char <= "\uff9f"
        for char in value
    )


def _looks_chinese(value: str) -> bool:
    return _has_obvious_chinese(value) and not _looks_japanese(value)


def _has_obvious_chinese(value: str) -> bool:
    if _looks_japanese(value):
        return False
    chinese_markers = (
        "这个", "那个", "如果", "因为", "所以", "应该", "节点", "换行", "字符串",
        "看看", "可以", "需要", "无法", "错误", "原因", "里面", "直接",
        "我看", "你可以", "是什么", "为什么", "怎么样",
    )
    chinese_punctuation = "，。？！；：、"
    common_chinese_chars = set("我你的是了在有和不这那们把里吗吧呢")
    simplified_only_chars = set("语错该节显这们为会览")
    return any(marker in value for marker in chinese_markers) or any(
        char in chinese_punctuation for char in value
    ) or sum(1 for char in value if char in common_chinese_chars) >= 2 or any(
        char in simplified_only_chars for char in value
    )


def parse_structured_json(content: str) -> StructuredJsonParseResult:
    if not content.strip():
        return StructuredJsonParseResult(None, "empty")
    try:
        value, repaired = load_json_object(content)
        return StructuredJsonParseResult(value, "valid", deterministic_repair=repaired)
    except ValueError:
        return StructuredJsonParseResult(None, "invalid_json" if _looks_structured_reply(content) else "text")


def _looks_structured_reply(content: str) -> bool:
    return content.lstrip().startswith(("{", "[", "```")) or '"segments"' in content or "'segments'" in content


def _build_safe_parse_failure_reply() -> ChatReply:
    return ChatReply(
        [
            ChatSegment(
                SAFE_PARSE_FAILURE_TEXT,
                DEFAULT_TONE,
                SAFE_PARSE_FAILURE_TRANSLATION,
                suppress_tts=True,
            )
        ]
    )
