from __future__ import annotations

from app.backchannel.classifier import RuleClassifier
from app.backchannel.model_cache import backchannel_model_cache_kwargs
from app.backchannel.models import BackchannelLabel
from app.backchannel.probe_classifier import ProbeIntentClassifier


class HybridBackchannelClassifier:
    """rules-first hybrid classifier。

    规则层负责高精度信号(问候/报错等关键词);probe 层(bge 句向量 +
    标注数据训练出的分类头)补足规则无命中的中文情感/意图泛化。
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
        rule_label = self._rule_classifier.classify(text)
        if rule_label is not None:
            return rule_label

        result = self._probe_classifier.classify_intent(text)
        if result is None:
            return None
        intent, confidence = result
        emotion = self._rule_classifier.classify_emotion_for_intent(text, intent)
        return BackchannelLabel(intent=intent, emotion=emotion, confidence=confidence)
