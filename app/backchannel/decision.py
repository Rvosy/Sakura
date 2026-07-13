"""不依赖 Qt 的快速接话分类与模板选择。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.backchannel.models import BackchannelLabel
from app.backchannel.resolver import BackchannelChoice, TemplateResolver


class BackchannelClassifier(Protocol):
    def classify(self, text: str) -> BackchannelLabel | None: ...


ClassifiedCallback = Callable[
    [str, BackchannelLabel | None, BackchannelChoice | None],
    None,
]


class BackchannelDecisionService:
    def __init__(
        self,
        classifier: BackchannelClassifier,
        *,
        on_classified: ClassifiedCallback | None = None,
    ) -> None:
        self.classifier = classifier
        self.resolver: TemplateResolver | None = None
        self.on_classified = on_classified

    def classify(self, text: str) -> BackchannelLabel | None:
        return self.classifier.classify(text)

    def resolve(
        self,
        text: str,
        label: BackchannelLabel | None,
    ) -> BackchannelChoice | None:
        choice = self.resolver.resolve(label) if self.resolver is not None else None
        if self.on_classified is not None:
            self.on_classified(text, label, choice)
        return choice
