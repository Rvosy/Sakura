from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 硬约束:分类器输出标签集与模板键标签集必须是同一张表,
# 否则会产生永远不可达的死条目(标签漂移在草案审查中已实际出现过)。
INTENTS = (
    "question",
    "request",
    "error",
    "complaint",
    "support",
    "positive",
    "affection",
    # 社交礼仪家族(ISO 24617-2 Social Obligations Management 维度):
    # 子类是独立交际功能(报到/早安/晚间/睡前),无精确模板时回退家族根 greeting。
    "greeting",
    "greeting_return",
    "greeting_morning",
    "greeting_evening",
    "greeting_goodnight",
)
FALLBACK_INTENT = "fallback"
EMOTIONS = (
    "neutral",
    "confused",
    "anxious",
    "frustrated",
    "sad",
    "angry",
    "happy",
    "playful",
    "embarrassed",
)
PHASES = ("repeated_issue", "tool_running", "long_wait")

DEFAULT_EMOTION = "neutral"


@dataclass(frozen=True)
class BackchannelLabel:
    """分类器输出:用户意图 + 用户情绪 + 置信度。

    user_emotion 与角色回应语气(模板 tone)是两个概念,不可混用:
    用户生气时角色更应该用安抚语气而非不满语气。
    """

    intent: str
    emotion: str = DEFAULT_EMOTION
    confidence: float = 0.0


@dataclass(frozen=True)
class BackchannelVariant:
    """一条接话变体:ja/zh 成对出现。

    音频只从 ja 合成(角色 TTS text_lang=ja),字幕可能显示 zh,
    成对结构保证"正在播的音频 ↔ 显示的字幕"始终对应。
    """

    ja: str
    zh: str
    audio: str | None = None

    def display_text(self, subtitle_language: str) -> str:
        """按字幕语言返回显示文本;缺中文时回退日文(与 ChatSegment 行为一致)。"""
        if subtitle_language == "zh" and self.zh.strip():
            return self.zh.strip()
        return self.ja


@dataclass(frozen=True)
class BackchannelTemplate:
    """一个接话模板:匹配键(phase / intent+emotion)→ 表现(tone/portrait)+ 变体池。

    带 phase 的条目按相位优先匹配;纯相位条目可不带 intent/emotion。
    intent 为 FALLBACK_INTENT 的条目构成兜底池(闲聊与低置信输入也落这里)。
    """

    id: str
    tone: str
    portrait: str
    variants: tuple[BackchannelVariant, ...]
    intent: str | None = None
    emotion: str | None = None
    phase: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.intent == FALLBACK_INTENT


@dataclass(frozen=True)
class BackchannelManifest:
    """一个角色的接话模板清单(角色包层;本地 overlay 合并留待后续阶段)。"""

    templates: tuple[BackchannelTemplate, ...]
    character_id: str = ""
    source_path: Path | None = None

    @property
    def fallback_templates(self) -> tuple[BackchannelTemplate, ...]:
        return tuple(template for template in self.templates if template.is_fallback)

    def __bool__(self) -> bool:
        return bool(self.templates)
