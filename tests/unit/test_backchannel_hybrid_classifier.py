from __future__ import annotations

from app.backchannel.hybrid_classifier import HybridBackchannelClassifier
from app.backchannel.models import BackchannelLabel


class ProbeStub:
    def __init__(self, result: tuple[str, float] | None) -> None:
        self.result = result
        self.calls: list[str] = []

    def classify_intent(self, text: str) -> tuple[str, float] | None:
        self.calls.append(text)
        return self.result


def test_hybrid_keeps_rule_classifier_priority() -> None:
    probe = ProbeStub(("request", 0.99))
    classifier = HybridBackchannelClassifier(probe_classifier=probe)  # type: ignore[arg-type]

    label = classifier.classify("报错了,又失败")

    assert label is not None
    assert label.intent == "error"
    assert probe.calls == []


def test_hybrid_uses_probe_when_rules_have_no_signal() -> None:
    classifier = HybridBackchannelClassifier(
        probe_classifier=ProbeStub(("request", 0.91))  # type: ignore[arg-type]
    )

    label = classifier.classify("麻烦整理这段会议内容")

    assert label == BackchannelLabel(intent="request", emotion="neutral", confidence=0.91)


def test_hybrid_probe_intent_still_uses_rule_emotion_layer() -> None:
    classifier = HybridBackchannelClassifier(
        probe_classifier=ProbeStub(("request", 0.91))  # type: ignore[arg-type]
    )

    label = classifier.classify("不好意思,麻烦整理这段会议内容")

    assert label == BackchannelLabel(intent="request", emotion="embarrassed", confidence=0.91)


def test_hybrid_returns_none_when_both_layers_abstain() -> None:
    classifier = HybridBackchannelClassifier(
        probe_classifier=ProbeStub(None)  # type: ignore[arg-type]
    )

    assert classifier.classify("今天天气不错") is None


def test_hybrid_preload_safe_and_delegated() -> None:
    # 1. Safe when probe_classifier has no preload method
    classifier = HybridBackchannelClassifier(
        probe_classifier=ProbeStub(None)  # type: ignore[arg-type]
    )
    classifier.preload()  # Should not crash

    # 2. Delegated when probe_classifier has preload method
    class PreloadableProbeStub(ProbeStub):
        def __init__(self, result: tuple[str, float] | None) -> None:
            super().__init__(result)
            self.preloaded = False

        def preload(self) -> None:
            self.preloaded = True

    stub = PreloadableProbeStub(None)
    classifier_preloadable = HybridBackchannelClassifier(
        probe_classifier=stub  # type: ignore[arg-type]
    )
    classifier_preloadable.preload()
    assert stub.preloaded is True
