from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from app.backchannel.embedding_classifier import EmbeddingIntentClassifier
from app.backchannel.prototypes import IntentPrototype


class FakeEncoder:
    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self.vectors = vectors
        self.calls: list[tuple[str, ...]] = []

    def encode(self, sentences: Sequence[str], **_kwargs: Any) -> list[tuple[float, ...]]:
        texts = tuple(sentences)
        self.calls.append(texts)
        return [self.vectors.get(text, (0.0, 0.0)) for text in texts]


def _prototype(intent: str, text: str) -> IntentPrototype:
    return IntentPrototype(intent=intent, text=text, source="test")


def test_embedding_classifier_returns_nearest_intent_when_confident() -> None:
    encoder = FakeEncoder(
        {
            "帮我查天气": (1.0, 0.0),
            "这个是什么意思": (0.0, 1.0),
            "麻烦查一下天气": (0.99, 0.03),
        }
    )
    classifier = EmbeddingIntentClassifier(
        prototypes=(
            _prototype("request", "帮我查天气"),
            _prototype("question", "这个是什么意思"),
        ),
        encoder=encoder,
        threshold=0.8,
        margin=0.2,
    )

    result = classifier.classify_intent("麻烦查一下天气")
    assert result is not None
    assert result[0] == "request"
    # 直接比绝对差,绕开 numpy 被前置测试加载后 pytest.approx 的内部歧义
    assert abs(result[1] - 0.999541) < 1e-6


def test_embedding_classifier_rejects_close_second_best() -> None:
    encoder = FakeEncoder(
        {
            "帮我查天气": (1.0, 0.0),
            "这个是什么意思": (0.0, 1.0),
            "这个能帮我看看吗": (0.72, 0.69),
        }
    )
    classifier = EmbeddingIntentClassifier(
        prototypes=(
            _prototype("request", "帮我查天气"),
            _prototype("question", "这个是什么意思"),
        ),
        encoder=encoder,
        threshold=0.5,
        margin=0.08,
    )

    assert classifier.classify_intent("这个能帮我看看吗") is None


def test_embedding_classifier_rejects_low_similarity() -> None:
    encoder = FakeEncoder(
        {
            "帮我查天气": (1.0, 0.0),
            "这个是什么意思": (0.0, 1.0),
            "路过看看": (0.0, 0.0),
        }
    )
    classifier = EmbeddingIntentClassifier(
        prototypes=(
            _prototype("request", "帮我查天气"),
            _prototype("question", "这个是什么意思"),
        ),
        encoder=encoder,
        threshold=0.8,
        margin=0.1,
    )

    assert classifier.classify_intent("路过看看") is None


def test_embedding_classifier_factory_failure_degrades_to_none() -> None:
    def fail() -> FakeEncoder:
        raise RuntimeError("missing model")

    classifier = EmbeddingIntentClassifier(
        prototypes=(_prototype("request", "帮我查天气"),),
        encoder_factory=fail,
    )

    assert classifier.classify_intent("帮我查天气") is None
    assert classifier.available is False
