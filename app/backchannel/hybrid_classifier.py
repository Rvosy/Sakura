from __future__ import annotations

from app.backchannel.classifier import RuleClassifier
from app.backchannel.model_cache import backchannel_model_cache_kwargs
from app.backchannel.models import BackchannelLabel
from app.backchannel.probe_classifier import ProbeIntentClassifier


class HybridBackchannelClassifier:
    """probe-primary hybrid classifier。

    架构(2026-06-14 翻转):规则层只保留高精度前置快路径(程式化问候 + 无歧义
    技术报错 + 强吐槽 complaint 关键词);其余 support/positive/affection/request
    与一切语义/情感泛化交给 probe 层(bge 句向量 + 保守分类头,弃权即落中性兜底)。
    审计证实规则关键词子串匹配粗颗粒度、是错接主因,故大幅删减,只留 probe 实测
    难稳定接住的高精度信号。
    """

    # 首次 classify 会冷加载句向量模型(数秒),必须派发到后台线程。
    prefers_background = True

    def __init__(
        self,
        rule_classifier: RuleClassifier | None = None,
        probe_classifier: ProbeIntentClassifier | None = None,
    ) -> None:
        self._rule_classifier = rule_classifier if rule_classifier is not None else RuleClassifier()
        self._probe_classifier = (
            probe_classifier if probe_classifier is not None else ProbeIntentClassifier()
        )

    @classmethod
    def from_model_cache(cls, base_dir) -> "HybridBackchannelClassifier":  # type: ignore[no-untyped-def]
        return cls(
            probe_classifier=ProbeIntentClassifier(
                model_kwargs=backchannel_model_cache_kwargs(base_dir)
            )
        )

    def preload(self) -> None:
        """预加载底层 probe 头与句向量模型。"""
        preload_fn = getattr(self._probe_classifier, "preload", None)
        if callable(preload_fn):
            preload_fn()

    def classify(self, text: str) -> BackchannelLabel | None:
        # 前置快路径:仅闭集/结构化的高精度规则(问候 + 技术报错)。
        high_precision = self._rule_classifier.classify_high_precision(text)
        if high_precision is not None:
            return high_precision

        # 其余全部交给 probe(保守,弃权→None→中性兜底),避免规则粗颗粒度错接。
        result = self._probe_classifier.classify_intent(text)
        if result is None:
            return None
        intent, confidence = result
        emotion = self._rule_classifier.classify_emotion_for_intent(text, intent)
        return BackchannelLabel(intent=intent, emotion=emotion, confidence=confidence)
