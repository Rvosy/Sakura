from __future__ import annotations

from app.backchannel.classifier import RuleClassifier
from app.backchannel.embedding_classifier import EmbeddingIntentClassifier
from app.backchannel.model_cache import backchannel_model_cache_kwargs
from app.backchannel.models import BackchannelLabel
from app.backchannel.prototypes import load_runtime_intent_prototypes


class HybridBackchannelClassifier:
    """rules-first hybrid classifier。

    规则层负责高精度信号;embedding 层只补足规则无命中的中文意图泛化。
    """

    # 首次 classify 会冷加载句向量模型(数秒)+ 编码原型,必须派发到后台线程。
    prefers_background = True

    def __init__(
        self,
        rule_classifier: RuleClassifier | None = None,
        embedding_classifier: EmbeddingIntentClassifier | None = None,
    ) -> None:
        self._rule_classifier = rule_classifier if rule_classifier is not None else RuleClassifier()
        self._embedding_classifier = (
            embedding_classifier
            if embedding_classifier is not None
            else EmbeddingIntentClassifier()
        )

    @classmethod
    def from_model_cache(cls, base_dir) -> "HybridBackchannelClassifier":  # type: ignore[no-untyped-def]
        return cls(
            embedding_classifier=EmbeddingIntentClassifier(
                prototypes=load_runtime_intent_prototypes(base_dir),
                model_kwargs=backchannel_model_cache_kwargs(base_dir),
            )
        )

    def classify(self, text: str) -> BackchannelLabel | None:
        rule_label = self._rule_classifier.classify(text)
        if rule_label is not None:
            return rule_label

        result = self._embedding_classifier.classify_intent(text)
        if result is None:
            return None
        intent, confidence = result
        emotion = self._rule_classifier.classify_emotion_for_intent(text, intent)
        return BackchannelLabel(intent=intent, emotion=emotion, confidence=confidence)
