from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable, Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from app.backchannel.models import BackchannelLabel, BackchannelManifest
from app.backchannel.resolver import BackchannelChoice, TemplateResolver
from app.core.debug_log import debug_log

if TYPE_CHECKING:
    from app.config.settings_service import BackchannelSettings

DisplayCallback = Callable[[BackchannelChoice], None]
# 分类完成回调:用于评测日志,记录 (输入文本, 标签, 选中模板)。
ClassifiedCallback = Callable[[str, "BackchannelLabel | None", BackchannelChoice | None], None]


class BackchannelClassifier(Protocol):
    def classify(self, text: str) -> BackchannelLabel | None:
        ...


class _ClassifySignals(QObject):
    # token, label-or-None;在 worker 线程 emit,经 queued connection 回主线程。
    done = Signal(int, object)


class _ClassifyRunnable(QRunnable):
    """在 QThreadPool 线程跑一次 classify,结果带 token 回传主线程。"""

    def __init__(
        self,
        classifier: BackchannelClassifier,
        text: str,
        token: int,
        signals: _ClassifySignals,
    ) -> None:
        super().__init__()
        self._classifier = classifier
        self._text = text
        self._token = token
        self._signals = signals

    def run(self) -> None:  # noqa: D401
        try:
            label = self._classifier.classify(self._text)
        except Exception as exc:  # noqa: BLE001
            debug_log("Backchannel", "后台分类异常,本轮按无标签处理", {"error": str(exc)})
            label = None
        self._signals.done.emit(self._token, label)


class BackchannelController(QObject):
    """等待期接话调度:延迟 → 分类 → 匹配 → 显示;正式回复到达即取消。

    不直接依赖任何 UI 类:显示动作由 display 回调注入,宿主(PetWindow)
    决定怎么呈现。回调只应走轻量字幕/立绘路径——临时段绝不进入
    回复历史、聊天记录、LLM 上下文或分段播放队列。

    分类执行模式由分类器自报:规则分类(<10ms)在主线程 QTimer 回调里
    同步完成;声明 prefers_background=True 的分类器(hybrid,首次会冷加载
    句向量模型耗时数秒)派发到 QThreadPool,结果经信号回主线程,期间
    timeout_ms 作为安全网——超时即按无标签落兜底,不让已经迟到的接话
    再被慢分类拖住;模型仍在后台加载,下一轮自然用上。
    """

    def __init__(
        self,
        classifier: BackchannelClassifier,
        display: DisplayCallback,
        *,
        settings: "BackchannelSettings",
        rng: random.Random | None = None,
        on_classified: ClassifiedCallback | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._classifier = classifier
        self._display = display
        self._on_classified = on_classified
        self._settings = settings.normalized()
        self._rng = rng if rng is not None else random.Random()
        self._resolver: TemplateResolver | None = None
        self._pending_text = ""
        # armed 标志防住一个窄竞态:timeout 事件已入队但 cancel 先被处理。
        self._armed = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        # 后台分类(hybrid)用:token 单飞 + 超时兜底。
        self._classify_token = 0
        self._inflight_token: int | None = None
        self._inflight_text = ""
        self._classify_signals = _ClassifySignals(self)
        self._classify_signals.done.connect(self._on_classify_done)
        self._classify_timeout_timer = QTimer(self)
        self._classify_timeout_timer.setSingleShot(True)
        self._classify_timeout_timer.timeout.connect(self._on_classify_timeout)

    # --- 对外接口 -----------------------------------------------------------
    def set_manifest(self, manifest: BackchannelManifest | None) -> None:
        """启动/切换角色时注入清单;None 表示该角色 opt-out,功能空转。"""
        self.cancel()
        if manifest:
            self._resolver = TemplateResolver(manifest, rng=self._rng)
        else:
            self._resolver = None

    def set_settings(self, settings: "BackchannelSettings") -> None:
        self._settings = settings.normalized()
        if not self._settings.active:
            self.cancel()

    def set_classifier(self, classifier: BackchannelClassifier) -> None:
        self.cancel()
        self._classifier = classifier

    def schedule(self, text: str) -> None:
        """用户消息已发送:启动接话延迟计时。延迟内回复到达则被 cancel 跳过。"""
        self.cancel()
        if not self._settings.active or self._resolver is None:
            return
        if not (text or "").strip():
            return
        # 触发概率:防罐头感的调节阀。
        if self._settings.probability < 1.0 and self._rng.random() >= self._settings.probability:
            return
        self._pending_text = text
        self._armed = True
        self._timer.start(self._settings.delay_ms)

    def cancel(self) -> None:
        """正式回复到达/请求失败/重新发送:放弃本轮接话。幂等。

        in-flight 的后台分类 token 失效,迟到结果在 _on_classify_done 被丢弃。
        """
        self._armed = False
        self._timer.stop()
        self._classify_timeout_timer.stop()
        self._inflight_token = None

    @property
    def is_pending(self) -> bool:
        return self._armed or self._inflight_token is not None

    # --- 内部逻辑 -----------------------------------------------------------
    def _on_timeout(self) -> None:
        if not self._armed or self._resolver is None:
            return
        self._armed = False
        if getattr(self._classifier, "prefers_background", False):
            self._dispatch_async(self._pending_text)
            return
        label = self._classifier.classify(self._pending_text)
        self._finish_classification(self._pending_text, label)

    def _dispatch_async(self, text: str) -> None:
        self._classify_token += 1
        token = self._classify_token
        self._inflight_token = token
        self._inflight_text = text
        timeout_ms = self._settings.timeout_ms
        if timeout_ms > 0:
            self._classify_timeout_timer.start(timeout_ms)
        runnable = _ClassifyRunnable(self._classifier, text, token, self._classify_signals)
        QThreadPool.globalInstance().start(runnable)

    def _on_classify_done(self, token: int, label: object) -> None:
        if token != self._inflight_token:
            return  # 已被 cancel/超时/新一轮取代
        self._inflight_token = None
        self._classify_timeout_timer.stop()
        self._finish_classification(self._inflight_text, label)  # type: ignore[arg-type]

    def _on_classify_timeout(self) -> None:
        if self._inflight_token is None:
            return
        # 丢弃 in-flight 真实结果(token 置空),本轮按无标签落兜底。
        self._inflight_token = None
        debug_log("Backchannel", "后台分类超时,本轮按无标签落兜底")
        self._finish_classification(self._inflight_text, None)

    def _finish_classification(self, text: str, label: "BackchannelLabel | None") -> None:
        if self._resolver is None:
            return
        # phase 参数有意不传:相位(repeated_issue/tool_running/long_wait)
        # 由后续迭代的会话相位跟踪器提供,v1 相位条目仅随清单预置。
        choice = self._resolver.resolve(label)
        if self._on_classified is not None:
            self._on_classified(text, label, choice)
        if choice is not None:
            self._display(choice)
