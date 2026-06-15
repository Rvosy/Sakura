from __future__ import annotations

import re

from app.backchannel.emotion import EmotionScorer
from app.backchannel.models import DEFAULT_EMOTION, BackchannelLabel

# 规则分类器:零依赖、零模型,目标 <10ms。
# 设计原则:情绪线索在表层特征(标点/语气词/emoji),
# 规则比 embedding 更擅长;意图的 embedding 原型分类留给 hybrid 模式(v2)。
# 词表只能输出 models.INTENTS / models.EMOTIONS 中的标签(词表对齐硬约束)。

# 意图关键词。匹配计数决定置信度;多意图命中时按 _INTENT_PRIORITY 决胜。
_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    # 仅保留无歧义的技术故障信号。已剔除审计证实的高假阳性子串:
    # "崩"(心态崩溃→support)、"坏了"(灯泡/心情坏了→none)、"不工作"(不想工作)、
    # "还是不行/又不行"(泛化不满,非报错)——它们把情绪/闲聊误接成 error(原精度仅 35%)。
    # "404"/"500" 也已移出裸词表(裸子串会把「人均500」「400-500元」误判 error),
    # 改由 _http_status_error 做上下文门控(需 http/接口/报错等线索词)。
    "error": (
        "报错", "出错", "错误", "bug", "Bug", "BUG", "error", "Error",
        "Traceback", "traceback", "exception", "Exception", "闪退",
        "失败", "跑不起来", "运行不了", "无法运行", "无法打开",
    ),
    "complaint": (
        "好烦", "很烦", "真烦", "烦死", "烦人", "气死", "讨厌", "受不了",
        "无语", "服了", "恶心", "垃圾", "难用", "卡死", "什么玩意",
    ),
    "support": (
        "难过", "想哭", "哭了", "累了", "好累", "心情不好", "emo", "难受",
        "委屈", "睡不着", "压力好大", "撑不住",
    ),
    # 剔除"可爱"(过泛:可爱的猫/这个设计很可爱,非对角色亲昵,精度仅 29%)。
    "affection": (
        "喜欢你", "爱你", "想你", "抱抱", "亲亲", "摸摸", "贴贴",
    ),
    "request": (
        "帮我", "给我", "替我", "麻烦你", "搜一下", "搜索", "查一下", "查查",
        "打开", "写一个", "写个", "做一个", "生成", "翻译", "总结", "整理一下",
    ),
    "positive": (
        "成功", "搞定", "解决了", "太好了", "好耶", "通过了", "完成了",
        "跑通了", "可以了", "哈哈",
    ),
}

# 多意图命中同分时的决胜顺序:特异性强的信号优先。
# 不含 question:疑问句没有可靠的接话信号(偏好/事实/闲聊都带疑问词),
# 旧逻辑把它们硬判成 question→confused 立绘,是「错接」的主要来源。
# 现交给 probe 层做保守泛化:命中不了具体情绪/社交信号就落 none 中性兜底。
_INTENT_PRIORITY = (
    "error", "complaint", "support", "affection", "request", "positive",
)

# 情绪信号,按优先级检查,首个命中即采用。
_EXCLAMATION_RUN = re.compile(r"[!！]{2,}")
_CODE_FENCE = "```"

# HTTP 状态码(4xx/5xx)上下文门控:只有同时出现线索词才算报错信号,
# 否则「人均500」「400-500元」会被裸子串误判 error。
_HTTP_STATUS_RE = re.compile(r"(?<!\d)[45]\d{2}(?!\d)")
_HTTP_ERROR_CUES = (
    "http", "HTTP", "接口", "请求", "报错", "错误", "异常", "状态",
    "服务器", "网关", "响应", "超时", "返回码", "code",
)

# 社交礼仪句(greeting 家族):高度程式化的封闭集,会话分析中的相邻对首件。
# 仅对短输入短路(长句里"我回来了,帮我查…"应让任务意图按正常计分胜出)。
_GREETING_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("greeting_goodnight", ("晚安", "去睡了", "睡觉去", "我先睡", "去休息了")),
    ("greeting_return", ("回来了", "回来啦", "我回啦", "我回来", "到家", "下班", "放学")),
    ("greeting_morning", ("早上好", "早安", "早哇", "我醒了", "起床了")),
    ("greeting_evening", ("晚上好",)),
    # 英文词只留小写:_is_complete_greeting 对输入和关键词都 casefold
    ("greeting", ("你好", "您好", "哈喽", "嗨", "hello", "hi", "在吗", "在不在", "在么")),
)
_GREETING_MAX_LENGTH = 12
_GREETING_CONFIDENCE = 0.85
_GREETING_ALLOWED_SUFFIX = ("了", "啦", "呀", "啊", "呢", "哦", "喔", "哇", "哈", "咯")
_GREETING_STRIP_CHARS = re.compile(r"[\s,，.。!！?？~～…、]+")

_BASE_CONFIDENCE = 0.65
_CONFIDENCE_STEP = 0.15
_MAX_CONFIDENCE = 0.9

# 纯情绪表达(无任务意图关键词)的情绪→意图反推:
# 负面情绪寻求安慰/共情,正面情绪分享喜悦。confused/embarrassed
# 单独出现时语境太模糊,不反推(留给 fallback)。
_EMOTION_IMPLIED_INTENT: dict[str, str] = {
    "sad": "support",
    "anxious": "support",
    "angry": "complaint",
    "frustrated": "complaint",
    "happy": "positive",
    "playful": "positive",
}


class RuleClassifier:
    """零依赖规则分类器。返回 None 表示无可靠信号,调用方落兜底池。"""

    def __init__(self, emotion_scorer: EmotionScorer | None = None) -> None:
        self._emotion_scorer = emotion_scorer if emotion_scorer is not None else EmotionScorer()

    def classify(self, text: str) -> BackchannelLabel | None:
        content = (text or "").strip()
        if not content:
            return None

        greeting = self._classify_greeting(content)
        if greeting is not None:
            return greeting

        intent, hits = self._classify_intent(content)
        if intent is None:
            return self._classify_by_emotion_only(content)
        emotion = self._classify_emotion(content, intent)
        confidence = min(
            _MAX_CONFIDENCE,
            _BASE_CONFIDENCE + _CONFIDENCE_STEP * max(0, hits - 1),
        )
        return BackchannelLabel(intent=intent, emotion=emotion, confidence=confidence)

    def classify_high_precision(self, text: str) -> BackchannelLabel | None:
        """只返回闭集/结构化的高精度信号,供 probe-primary 架构做前置快路径。

        审计(2026-06-14)证实关键词子串匹配在 support 48% / positive 62% /
        request 68% 等语义意图上粗颗粒度、精度低,是错接主因,已下放 probe。
        仅保留三类高精度、且 probe 实测难稳定接住的信号:
        - greeting:程式化社交闭集(短句短路,精度 100%);
        - error:无歧义技术故障(已剪枝的高精度词表 + 代码块 / Traceback);
        - complaint:强吐槽关键词(烦死/垃圾/难用…,精度 83.6%)。probe 即便
          有 2300+ 条 complaint 数据仍常对「烦死了这破东西真难用」弃权,而这类
          清晰抱怨漏接体验差;complaint→support 的误判是「软错」(同为负面共情)。
        其余 support/positive/affection/request 一律交给 probe(弃权→中性兜底)。
        优先级 error > complaint,与原计分一致。
        """
        content = (text or "").strip()
        if not content:
            return None
        greeting = self._classify_greeting(content)
        if greeting is not None:
            return greeting
        for intent, hits in (
            ("error", self._error_signal_score(content)),
            ("complaint", self._keyword_hits("complaint", content)),
        ):
            if hits:
                confidence = min(
                    _MAX_CONFIDENCE,
                    _BASE_CONFIDENCE + _CONFIDENCE_STEP * max(0, hits - 1),
                )
                return BackchannelLabel(
                    intent=intent,
                    emotion=self._classify_emotion(content, intent),
                    confidence=confidence,
                )
        return None

    def _keyword_hits(self, intent: str, content: str) -> int:
        return sum(1 for keyword in _INTENT_KEYWORDS[intent] if keyword in content)

    def _http_status_error(self, content: str) -> bool:
        # 4xx/5xx 状态码只在有 http/接口/报错等上下文线索时才算 error 信号,
        # 避免「人均500」「400-500元」「预算500」这类价格/数量裸数字误触。
        if not _HTTP_STATUS_RE.search(content):
            return False
        return any(cue in content for cue in _HTTP_ERROR_CUES)

    def _error_signal_score(self, content: str) -> int:
        hits = self._keyword_hits("error", content)
        if _CODE_FENCE in content or '  File "' in content:
            hits += 2
        if self._http_status_error(content):
            hits += 1
        return hits

    def classify_emotion_for_intent(self, text: str, intent: str) -> str:
        """按规则层语义为已知 intent 补用户情绪。"""

        return self._classify_emotion((text or "").strip(), intent)

    def _classify_by_emotion_only(self, content: str) -> BackchannelLabel | None:
        """意图无关键词但情绪信号过阈值时,由情绪反推意图。

        "不开心""心态崩了"这类纯情绪表达没有任务意图,落 fallback 的
        中性确认("嗯。")等于没接上;情绪→意图的映射让它们落到
        安抚/共情模板。
        """
        emotion = self._emotion_scorer.best(content)
        if emotion is None:
            return None
        intent = _EMOTION_IMPLIED_INTENT.get(emotion)
        if intent is None:
            return None
        return BackchannelLabel(intent=intent, emotion=emotion, confidence=_BASE_CONFIDENCE)

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
        # HTTP 状态码需上下文门控,避免价格/数量裸数字误判 error。
        if self._http_status_error(content):
            scores["error"] = scores.get("error", 0) + 1
        if not scores:
            return None, 0
        best = max(scores.values())
        for intent in _INTENT_PRIORITY:
            if scores.get(intent) == best:
                return intent, best
        return None, 0

    def _classify_emotion(self, content: str, intent: str) -> str:
        # 情感打分制(EmotionScorer):词典累计打分,过阈值才采信;
        # 无可靠信号时回退感叹号规则与意图缺省映射(保持 v1 行为)。
        scored = self._emotion_scorer.best(content)
        if scored is not None:
            return scored
        if _EXCLAMATION_RUN.search(content):
            # 连续感叹号:正面意图按高兴算,其余按生气算。
            return "happy" if intent == "positive" else "angry"
        if intent == "affection":
            # 表白/亲昵语境的情绪缺省:害羞(对应模板键 embarrassed)。
            return "embarrassed"
        if intent == "complaint":
            return "angry"
        if intent == "support":
            return "sad"
        if intent == "positive":
            return "happy"
        if intent == "error":
            return "frustrated"
        return DEFAULT_EMOTION
