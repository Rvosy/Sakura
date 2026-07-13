"""无 Qt 的快速接话延迟、分类、选择与取消服务。"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.backchannel.decision import BackchannelClassifier, BackchannelDecisionService
from app.backchannel.models import BackchannelManifest
from app.backchannel.resolver import BackchannelChoice, TemplateResolver


ChoiceCallback = Callable[[BackchannelChoice], None]


class HeadlessBackchannelService:
    def __init__(
        self,
        classifier: BackchannelClassifier,
        manifest: BackchannelManifest,
        *,
        settings: object,
        on_choice: ChoiceCallback,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.manifest = manifest
        self.on_choice = on_choice
        self.rng = rng or random.Random()
        self.decision = BackchannelDecisionService(classifier)
        self.decision.resolver = TemplateResolver(manifest, rng=self.rng)
        self._classifier = classifier
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._token = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sakura-backchannel")

    def schedule(self, text: str) -> None:
        content = str(text or "").strip()
        self.cancel()
        if not content or self._closed or not bool(getattr(self.settings, "active", False)):
            return
        probability = max(0.0, min(1.0, float(getattr(self.settings, "probability", 1.0))))
        if probability < 1.0 and self.rng.random() >= probability:
            return
        with self._lock:
            self._token += 1
            token = self._token
            delay = max(0, int(getattr(self.settings, "delay_ms", 0))) / 1000
            timer = threading.Timer(delay, self._classify, args=(token, content))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def cancel(self) -> None:
        with self._lock:
            self._token += 1
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _classify(self, token: int, text: str) -> None:
        with self._lock:
            if self._closed or token != self._token:
                return
            self._timer = None
        if getattr(self._classifier, "prefers_background", False):
            self._executor.submit(self._classify_background, token, text)
            return
        self._finish(token, text, self.decision.classify(text))

    def _classify_background(self, token: int, text: str) -> None:
        label = self.decision.classify(text)
        self._finish(token, text, label)

    def _finish(self, token: int, text: str, label: object) -> None:
        with self._lock:
            if self._closed or token != self._token:
                return
            choice = self.decision.resolve(text, label)  # type: ignore[arg-type]
        if choice is not None:
            self.on_choice(choice)
