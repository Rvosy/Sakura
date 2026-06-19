from __future__ import annotations

import random
import threading

import pytest

pytest.importorskip("PySide6.QtWidgets")

from app.backchannel.classifier import RuleClassifier  # noqa: E402
from app.backchannel.controller import BackchannelController  # noqa: E402
from app.backchannel.models import (  # noqa: E402
    BackchannelManifest,
    BackchannelTemplate,
    BackchannelVariant,
)
from app.backchannel.resolver import BackchannelChoice  # noqa: E402
from app.config.settings_service import BackchannelSettings  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _manifest() -> BackchannelManifest:
    return BackchannelManifest(
        templates=(
            BackchannelTemplate(
                id="fb",
                tone="中性",
                portrait="站立待机",
                variants=(BackchannelVariant(ja="うん。", zh="嗯。"),),
                intent="fallback",
            ),
            BackchannelTemplate(
                id="err",
                tone="不满",
                portrait="不满无语",
                variants=(BackchannelVariant(ja="見てみる。", zh="我看看。"),),
                intent="error",
                emotion="frustrated",
            ),
        )
    )


def _make(
    displayed: list[BackchannelChoice],
    *,
    enabled: bool = True,
    probability: float = 1.0,
    manifest: BackchannelManifest | None = None,
) -> BackchannelController:
    controller = BackchannelController(
        RuleClassifier(),
        displayed.append,
        settings=BackchannelSettings(enabled=enabled, probability=probability),
        rng=random.Random(7),
    )
    if manifest is not None:
        controller.set_manifest(manifest)
    return controller


def test_schedule_arms_timer_and_timeout_displays() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, manifest=_manifest())
    controller.schedule("报错了,又失败")
    assert controller.is_pending
    assert controller._timer.isActive()
    # 直接触发超时回调(不等真实计时),与现有 bubble_auto_hide 测试风格一致。
    controller._on_timeout()
    assert len(displayed) == 1
    assert displayed[0].template.id == "err"
    assert not controller.is_pending


def test_chat_text_falls_to_fallback_template() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, manifest=_manifest())
    controller.schedule("今天天气不错")  # 无分类信号 → 兜底池
    controller._on_timeout()
    assert len(displayed) == 1
    assert displayed[0].template.id == "fb"


def test_cancel_prevents_display() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, manifest=_manifest())
    controller.schedule("报错了")
    controller.cancel()
    assert not controller._timer.isActive()
    # 模拟 timeout 事件已入队但 cancel 先处理的窄竞态:直接调回调不应显示。
    controller._on_timeout()
    assert displayed == []


def test_disabled_settings_never_arm() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, enabled=False, manifest=_manifest())
    controller.schedule("报错了")
    assert not controller.is_pending
    assert not controller._timer.isActive()


def test_no_manifest_never_arms() -> None:
    # 角色未提供清单(opt-out)→ 功能空转。
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed)
    controller.schedule("报错了")
    assert not controller.is_pending


def test_zero_probability_never_arms() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, probability=0.0, manifest=_manifest())
    for _ in range(5):
        controller.schedule("报错了")
        assert not controller.is_pending


def test_blank_text_never_arms() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, manifest=_manifest())
    controller.schedule("   ")
    assert not controller.is_pending


def test_set_manifest_none_cancels_and_disarms() -> None:
    # 切换到 opt-out 角色:取消在途接话并停用。
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, manifest=_manifest())
    controller.schedule("报错了")
    controller.set_manifest(None)
    assert not controller.is_pending
    controller.schedule("报错了")
    assert not controller.is_pending


def test_set_settings_disable_cancels_pending() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    controller = _make(displayed, manifest=_manifest())
    controller.schedule("报错了")
    controller.set_settings(BackchannelSettings(enabled=False))
    assert not controller.is_pending
    controller._on_timeout()
    assert displayed == []


# --- 后台分类(hybrid prefers_background)路径 ---------------------------------

class _BackgroundClassifier:
    """声明走后台线程的分类器,classify 在 worker 线程被调用。"""

    prefers_background = True

    def __init__(self, label: object) -> None:
        self._label = label
        self.calls = 0

    def classify(self, text: str) -> object:
        self.calls += 1
        return self._label


def _make_async(
    displayed: list[BackchannelChoice],
    classifier: object,
    *,
    timeout_ms: int = 400,
    on_classified=None,  # type: ignore[no-untyped-def]
) -> BackchannelController:
    controller = BackchannelController(
        classifier,  # type: ignore[arg-type]
        displayed.append,
        settings=BackchannelSettings(enabled=True, mode="hybrid", timeout_ms=timeout_ms),
        rng=random.Random(7),
        on_classified=on_classified,
    )
    controller.set_manifest(_manifest())
    return controller


def _spin_until(predicate, timeout_ms: int = 2000) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer

    deadline = QDeadlineTimer(timeout_ms)
    while not predicate() and not deadline.hasExpired():
        QCoreApplication.processEvents()


def test_background_classify_dispatches_and_displays() -> None:
    from app.backchannel.models import BackchannelLabel

    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    classifier = _BackgroundClassifier(BackchannelLabel("error", "frustrated", 0.9))
    controller = _make_async(displayed, classifier)
    controller.schedule("随便什么")
    controller._on_timeout()  # 派发到后台,结果异步回主线程
    assert displayed == []  # 尚未完成
    _spin_until(lambda: len(displayed) == 1)
    assert displayed[0].template.id == "err"
    assert classifier.calls == 1
    assert not controller.is_pending


def test_background_classify_cancel_drops_late_result() -> None:
    from app.backchannel.models import BackchannelLabel

    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    classifier = _BackgroundClassifier(BackchannelLabel("error", "frustrated", 0.9))
    controller = _make_async(displayed, classifier)
    controller.schedule("随便什么")
    controller._on_timeout()
    controller.cancel()  # 回复已到达
    _spin_until(lambda: classifier.calls == 1)
    # worker 跑完但 token 已失效,迟到结果被丢弃
    from PySide6.QtCore import QCoreApplication
    for _ in range(5):
        QCoreApplication.processEvents()
    assert displayed == []


def test_background_classify_thread_is_managed_and_shutdown_waits() -> None:
    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    started = threading.Event()
    release = threading.Event()

    class _BlockingClassifier:
        prefers_background = True

        def classify(self, text: str) -> object:
            started.set()
            release.wait(2)
            return None

    controller = _make_async(displayed, _BlockingClassifier())
    controller.schedule("随便什么")
    controller._on_timeout()
    assert started.wait(1)

    with controller._classify_threads_lock:
        threads = tuple(controller._classify_threads)
    assert len(threads) == 1
    assert threads[0].daemon is False
    assert controller.shutdown(timeout=0) is False

    release.set()
    assert controller.shutdown(timeout=1) is True
    controller.schedule("关闭后不再调度")
    assert not controller.is_pending
    assert displayed == []


def test_background_classify_timeout_falls_back() -> None:
    import time

    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []

    class _SlowClassifier:
        prefers_background = True

        def classify(self, text: str) -> object:
            time.sleep(0.3)
            from app.backchannel.models import BackchannelLabel

            return BackchannelLabel("error", "frustrated", 0.9)

    controller = _make_async(displayed, _SlowClassifier(), timeout_ms=50)
    controller.schedule("随便什么")
    controller._on_timeout()
    # 超时前先落兜底
    _spin_until(lambda: len(displayed) == 1, timeout_ms=500)
    assert displayed[0].template.id == "fb"
    # 慢分类随后跑完,但 token 已失效不再二次显示
    _spin_until(lambda: False, timeout_ms=400)
    assert len(displayed) == 1


def test_on_classified_callback_records_trace() -> None:
    from app.backchannel.models import BackchannelLabel

    _qt_app_or_skip()
    displayed: list[BackchannelChoice] = []
    traces: list[tuple] = []
    classifier = _BackgroundClassifier(BackchannelLabel("error", "frustrated", 0.9))
    controller = _make_async(
        displayed,
        classifier,
        on_classified=lambda text, label, choice: traces.append((text, label, choice)),
    )
    controller.schedule("报错文本")
    controller._on_timeout()
    _spin_until(lambda: len(traces) == 1)
    text, label, choice = traces[0]
    assert text == "报错文本"
    assert label.intent == "error"
    assert choice is not None and choice.template.id == "err"
