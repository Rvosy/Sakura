from __future__ import annotations

from app.backchannel.hybrid_classifier import HybridBackchannelClassifier
from app.backchannel.models import BackchannelLabel


class EmbeddingStub:
    def __init__(self, result: tuple[str, float] | None) -> None:
        self.result = result
        self.calls: list[str] = []

    def classify_intent(self, text: str) -> tuple[str, float] | None:
        self.calls.append(text)
        return self.result


def test_hybrid_keeps_rule_classifier_priority() -> None:
    embedding = EmbeddingStub(("request", 0.99))
    classifier = HybridBackchannelClassifier(embedding_classifier=embedding)  # type: ignore[arg-type]

    label = classifier.classify("报错了,又失败")

    assert label is not None
    assert label.intent == "error"
    assert embedding.calls == []


def test_hybrid_uses_embedding_when_rules_have_no_signal() -> None:
    classifier = HybridBackchannelClassifier(
        embedding_classifier=EmbeddingStub(("request", 0.91))  # type: ignore[arg-type]
    )

    label = classifier.classify("麻烦整理这段会议内容")

    assert label == BackchannelLabel(intent="request", emotion="neutral", confidence=0.91)


def test_hybrid_returns_none_when_both_layers_abstain() -> None:
    classifier = HybridBackchannelClassifier(
        embedding_classifier=EmbeddingStub(None)  # type: ignore[arg-type]
    )

    assert classifier.classify("今天天气不错") is None


def test_hybrid_preload_safe_and_delegated() -> None:
    # 1. Safe when embedding_classifier has no preload method
    classifier = HybridBackchannelClassifier(
        embedding_classifier=EmbeddingStub(None)  # type: ignore[arg-type]
    )
    classifier.preload()  # Should not crash

    # 2. Delegated when embedding_classifier has preload method
    class PreloadableEmbeddingStub(EmbeddingStub):
        def __init__(self, result: tuple[str, float] | None) -> None:
            super().__init__(result)
            self.preloaded = False

        def preload(self) -> None:
            self.preloaded = True

    stub = PreloadableEmbeddingStub(None)
    classifier_preloadable = HybridBackchannelClassifier(
        embedding_classifier=stub  # type: ignore[arg-type]
    )
    classifier_preloadable.preload()
    assert stub.preloaded is True
