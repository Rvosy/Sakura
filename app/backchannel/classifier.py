from __future__ import annotations

import re

from app.backchannel.models import DEFAULT_EMOTION, BackchannelLabel

# 规则分类器:零依赖、零模型,目标 <10ms。
# 设计原则(FEAT.md §4):情绪线索在表层特征(标点/语气词/emoji),
# 规则比 embedding 更擅长;意图的 embedding 原型分类留给 hybrid 模式(v2)。
# 词表只能输出 models.INTENTS / models.EMOTIONS 中的标签(词表对齐硬约束)。

# 意图关键词。匹配计数决定置信度;多意图命中时按 _INTENT_PRIORITY 决胜。
_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "error": (
        "报错", "出错", "错误", "bug", "Bug", "BUG", "error", "Error",
        "Traceback", "traceback", "exception", "Exception", "崩", "闪退",
        "失败", "跑不起来", "运行不了", "不工作", "坏了", "404", "500",
        "还是不行", "又不行", "无法运行", "无法打开",
    ),
    "complaint": (
        "烦", "气死", "讨厌", "受不了", "无语", "服了", "恶心", "垃圾",
        "难用", "卡死", "什么玩意",
    ),
    "support": (
        "难过", "想哭", "哭了", "累了", "好累", "心情不好", "emo", "难受",
        "委屈", "睡不着", "压力好大", "撑不住",
    ),
    "affection": (
        "喜欢你", "爱你", "想你", "抱抱", "亲亲", "摸摸", "贴贴", "可爱",
    ),
    "request": (
        "帮我", "给我", "替我", "麻烦你", "搜一下", "搜索", "查一下", "查查",
        "打开", "写一个", "写个", "做一个", "生成", "翻译", "总结", "整理一下",
    ),
    "question": (
        "什么", "怎么", "为什么", "为啥", "如何", "哪里", "哪个", "是不是",
        "能不能", "可以吗",
    ),
    "positive": (
        "成功", "搞定", "解决了", "太好了", "好耶", "通过了", "完成了",
        "跑通了", "可以了", "哈哈",
    ),
}

# 多意图命中同分时的决胜顺序:特异性强的信号优先
#(报错/抱怨/求安慰的关键词比疑问词更不容易误触)。
_INTENT_PRIORITY = (
    "error", "complaint", "support", "affection", "request", "question", "positive",
)

# 情绪信号,按优先级检查,首个命中即采用。
_EMOTION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("angry", ("气死", "烦死", "靠", "妈的", "滚", "受不了")),
    ("sad", ("难过", "想哭", "哭了", "伤心", "委屈", "呜", "唉")),
    ("anxious", ("急", "赶时间", "deadline", "来不及", "马上要", "快点")),
    ("frustrated", ("还是不行", "又不行", "又失败", "怎么又", "试了好几次", "还是报错")),
    ("confused", ("搞不懂", "不明白", "看不懂", "懵", "奇怪", "为什么")),
    ("playful", ("嘿嘿", "233", "喵", "~", "～")),
    ("happy", ("哈哈", "好耶", "开心", "太好了", "成功")),
)

_EXCLAMATION_RUN = re.compile(r"[!！]{2,}")
_QUESTION_MARKS = re.compile(r"[?？]")
_CODE_FENCE = "```"

# 社交礼仪句(greeting 家族):高度程式化的封闭集,会话分析中的相邻对首件。
# 仅对短输入短路(长句里"我回来了,帮我查…"应让任务意图按正常计分胜出)。
_GREETING_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("greeting_goodnight", ("晚安", "去睡了", "睡觉去", "我先睡", "去休息了")),
    ("greeting_return", ("回来了", "回来啦", "我回啦", "我回来", "到家", "下班", "放学")),
    ("greeting_morning", ("早上好", "早安", "早哇", "我醒了", "起床了")),
    ("greeting_evening", ("晚上好",)),
    ("greeting", ("你好", "您好", "哈喽", "嗨", "hello", "Hello", "hi", "Hi", "在吗", "在不在", "在么")),
)
_GREETING_MAX_LENGTH = 12
_GREETING_CONFIDENCE = 0.85
_GREETING_ALLOWED_SUFFIX = ("了", "啦", "呀", "啊", "呢", "哦", "喔", "哇", "哈", "咯")
_GREETING_STRIP_CHARS = re.compile(r"[\s,，.。!！?？~～…、]+")

_BASE_CONFIDENCE = 0.65
_CONFIDENCE_STEP = 0.15
_MAX_CONFIDENCE = 0.9


class RuleClassifier:
    """零依赖规则分类器。返回 None 表示无可靠信号,调用方落兜底池。"""

    def classify(self, text: str) -> BackchannelLabel | None:
        content = (text or "").strip()
        if not content:
            return None

        greeting = self._classify_greeting(content)
        if greeting is not None:
            return greeting

        intent, hits = self._classify_intent(content)
        if intent is None:
            return None
        emotion = self._classify_emotion(content, intent)
        confidence = min(
            _MAX_CONFIDENCE,
            _BASE_CONFIDENCE + _CONFIDENCE_STEP * max(0, hits - 1),
        )
        return BackchannelLabel(intent=intent, emotion=emotion, confidence=confidence)

    def _classify_greeting(self, content: str) -> BackchannelLabel | None:
        # 程式化问候必须基本占满整句;短句里混入任务/情绪信号时交给计分器,
        # 让"在吗帮我查天气"按 request 处理,不被 greeting 抢走。
        normalized = _GREETING_STRIP_CHARS.sub("", content).casefold()
        if len(normalized) > _GREETING_MAX_LENGTH:
            return None
        for intent, keywords in _GREETING_PATTERNS:
            if any(self._is_complete_greeting(normalized, keyword) for keyword in keywords):
                return BackchannelLabel(
                    intent=intent,
                    emotion=DEFAULT_EMOTION,
                    confidence=_GREETING_CONFIDENCE,
                )
        return None

    def _is_complete_greeting(self, normalized: str, keyword: str) -> bool:
        base = _GREETING_STRIP_CHARS.sub("", keyword).casefold()
        if not base or not normalized.startswith(base):
            return False
        suffix = normalized[len(base):]
        return all(char in _GREETING_ALLOWED_SUFFIX for char in suffix)

    def _classify_intent(self, content: str) -> tuple[str | None, int]:
        scores: dict[str, int] = {}
        for intent, keywords in _INTENT_KEYWORDS.items():
            count = sum(1 for keyword in keywords if keyword in content)
            if count:
                scores[intent] = count
        # 代码块/报错栈是 error 的强信号(报错往往整段粘贴而不含中文关键词)。
        if _CODE_FENCE in content or "  File \"" in content:
            scores["error"] = scores.get("error", 0) + 2
        # 问号本身就是 question 信号,即便没有疑问词。
        if _QUESTION_MARKS.search(content):
            scores["question"] = scores.get("question", 0) + 1
        if not scores:
            return None, 0
        best = max(scores.values())
        for intent in _INTENT_PRIORITY:
            if scores.get(intent) == best:
                return intent, best
        return None, 0

    def _classify_emotion(self, content: str, intent: str) -> str:
        for emotion, signals in _EMOTION_SIGNALS:
            if any(signal in content for signal in signals):
                return emotion
        if _EXCLAMATION_RUN.search(content):
            # 连续感叹号:正面意图按高兴算,其余按生气算。
            return "happy" if intent == "positive" else "angry"
        if intent == "affection":
            # 表白/亲昵语境的情绪缺省:害羞(对应模板键 embarrassed)。
            return "embarrassed"
        if intent == "question":
            return "confused"
        if intent == "complaint":
            return "angry"
        if intent == "support":
            return "sad"
        if intent == "positive":
            return "happy"
        if intent == "error":
            return "frustrated"
        return DEFAULT_EMOTION
