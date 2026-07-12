from __future__ import annotations

import random
import multiprocessing
from typing import TYPE_CHECKING, Callable, Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from app.backchannel.models import BackchannelLabel, BackchannelManifest
from app.backchannel.resolver import BackchannelChoice, TemplateResolver
from app.core.runtime_log import log_event

if TYPE_CHECKING:
    from app.config.settings_service import BackchannelSettings
    from app.core.resource_manager import ResourceManager

DisplayCallback = Callable[[BackchannelChoice], None]
# 分类完成回调:用于评测日志,记录 (输入文本, 标签, 选中模板)。
ClassifiedCallback = Callable[[str, "BackchannelLabel | None", BackchannelChoice | None], None]


class BackchannelClassifier(Protocol):
    def classify(self, text: str) -> BackchannelLabel | None:
        ...


class _ClassifySignals(QObject):
    # token, label-or-None;在 worker 线程 emit,经 queued connection 回主线程。
    done = Signal(int, object)


class BackchannelController(QObject):
    """等待期接话调度:延迟 → 分类 → 匹配 → 显示;正式回复到达即取消。

    不直接依赖任何 UI 类:显示动作由 display 回调注入,宿主(PetWindow)
    决定怎么呈现。回调只应走轻量字幕/立绘路径——临时段绝不进入
    回复历史、聊天记录、LLM 上下文或分段播放队列。

    分类执行模式由分类器自报:规则分类(<10ms)在主线程 QTimer 回调里
    同步完成;声明 prefers_background=True 的分类器(hybrid,首次会冷加载
    句向量模型耗时数秒)派发到受控 Python 线程,结果经信号回主线程,期间
    timeout_ms 作为安全网——超时即按无标签落兜底,不让已经迟到的接话
    再被慢分类拖住;模型仍在后台加载,下一轮自然用上。线程不会设为 daemon，
    宿主关闭时通过 shutdown() 等待已启动任务结束，避免解释器退出时截断模型原生资源。
    """

    def __init__(
        self,
        classifier: BackchannelClassifier,
        display: DisplayCallback,
        *,
        settings: "BackchannelSettings",
        resource_manager: "ResourceManager",
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
        self._process_poll_timer = QTimer(self)
        self._process_poll_timer.setInterval(25)
        self._process_poll_timer.timeout.connect(self._poll_classifier_process)
        self._process_startup_timer = QTimer(self)
        self._process_startup_timer.setSingleShot(True)
        self._process_startup_timer.timeout.connect(self._on_classifier_process_startup_timeout)
        self._classifier_process = None
        self._process_commands = None
        self._process_results = None
        self._process_ready = False
        self._process_pending: tuple[int, str] | None = None
        self._shutdown = False
        self._thread_group = resource_manager.track_thread_group(
            cancel=self._begin_shutdown,
            label="backchannel",
        )

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
        self._stop_classifier_process()
        self._classifier = classifier

    def schedule(self, text: str) -> None:
        """用户消息已发送:启动接话延迟计时。延迟内回复到达则被 cancel 跳过。"""
        self.cancel()
        if self._shutdown or not self._settings.active or self._resolver is None:
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
        had_inflight = self._inflight_token is not None
        self._armed = False
        self._timer.stop()
        self._classify_timeout_timer.stop()
        self._inflight_token = None
        self._process_pending = None
        if self._classifier_process is not None and (had_inflight or not self._process_ready):
            self._stop_classifier_process()

    def shutdown(self, timeout: float | None = None) -> bool:
        """停止接收新任务并等待已启动的分类线程结束。

        返回值表示线程是否已全部退出。超时后线程仍保持非 daemon，会自然完成，
        但不再向已关闭的控制器投递结果。
        """
        timeout_ms = None if timeout is None else max(0, int(timeout * 1000))
        self._stop_classifier_process()
        return self._thread_group.stop(timeout_ms)

    def _begin_shutdown(self) -> None:
        """进入终态并立即失效所有待处理或在飞分类结果。"""
        self._shutdown = True
        self.cancel()

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
        if self._shutdown:
            return
        self._classify_token += 1
        token = self._classify_token
        self._inflight_token = token
        self._inflight_text = text
        if getattr(self._classifier, "process_base_dir", None) is not None:
            self._dispatch_process(token, text)
            return
        timeout_ms = self._settings.timeout_ms
        if timeout_ms > 0:
            self._classify_timeout_timer.start(timeout_ms)

        def run_classification() -> None:
            try:
                label = self._classifier.classify(text)
            except Exception as exc:  # noqa: BLE001
                log_event("Backchannel", "后台分类异常,本轮按无标签处理", {"error": str(exc)})
                label = None
            if not self._shutdown:
                try:
                    self._classify_signals.done.emit(token, label)
                except RuntimeError:
                    # QObject 可能已随宿主窗口销毁；关闭阶段不再投递结果。
                    pass

        thread = self._thread_group.spawn(
            run_classification,
            name=f"sakura-backchannel-{token}",
        )
        if thread is None:
            # 关闭与派发窄竞态：线程组进入终态后不保留 pending token。
            self.cancel()

    def _dispatch_process(self, token: int, text: str) -> None:
        if self._classifier_process is None:
            try:
                self._start_classifier_process()
            except Exception as exc:  # noqa: BLE001
                log_event("Backchannel", "分类子进程启动失败", {"error": str(exc)})
                self._stop_classifier_process()
        if self._classifier_process is None:
            self._finish_classification(text, None)
            return
        self._process_pending = (token, text)
        if self._process_ready:
            self._send_process_request()

    def _start_classifier_process(self) -> None:
        from app.backchannel.process_worker import run_hybrid_classifier_worker

        context = multiprocessing.get_context("spawn")
        result_parent, result_child = context.Pipe(duplex=False)
        command_child, command_parent = context.Pipe(duplex=False)
        process = context.Process(
            target=run_hybrid_classifier_worker,
            args=(str(getattr(self._classifier, "process_base_dir")), command_child, result_child),
            name="sakura-backchannel-classifier",
            daemon=True,
        )
        process.start()
        command_child.close()
        result_child.close()
        self._classifier_process = process
        self._process_commands = command_parent
        self._process_results = result_parent
        self._process_ready = False
        self._process_poll_timer.start()
        self._process_startup_timer.start(30_000)

    def _send_process_request(self) -> None:
        if self._process_pending is None or self._process_commands is None:
            return
        token, text = self._process_pending
        self._process_pending = None
        try:
            self._process_commands.send((token, text))
        except (BrokenPipeError, EOFError, OSError):
            self._stop_classifier_process()
            self._on_classify_done(token, None)
            return
        timeout_ms = self._settings.timeout_ms
        if timeout_ms > 0:
            self._classify_timeout_timer.start(timeout_ms)

    def _poll_classifier_process(self) -> None:
        connection = self._process_results
        if connection is None:
            return
        try:
            while connection.poll():
                kind, token, payload = connection.recv()
                if kind == "ready":
                    self._process_startup_timer.stop()
                    self._process_ready = True
                    self._send_process_request()
                elif kind == "result":
                    self._on_classify_done(int(token), payload)
                elif kind in {"error", "startup_error"}:
                    log_event("Backchannel", "分类子进程失败", {"error": str(payload)})
                    if token is not None:
                        self._on_classify_done(int(token), None)
                    else:
                        self._stop_classifier_process()
        except (EOFError, OSError):
            token = self._inflight_token
            self._stop_classifier_process()
            if token is not None:
                self._on_classify_done(token, None)

    def _stop_classifier_process(self) -> None:
        self._process_poll_timer.stop()
        self._process_startup_timer.stop()
        for connection in (self._process_commands, self._process_results):
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        process = self._classifier_process
        if process is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
            process.close()
        self._classifier_process = None
        self._process_commands = None
        self._process_results = None
        self._process_ready = False
        self._process_pending = None

    def _on_classifier_process_startup_timeout(self) -> None:
        token = self._inflight_token
        text = self._inflight_text
        self._inflight_token = None
        self._stop_classifier_process()
        log_event("Backchannel", "分类子进程启动超时,本轮按无标签处理")
        if token is not None:
            self._finish_classification(text, None)

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
        if self._classifier_process is not None:
            self._stop_classifier_process()
        log_event("Backchannel", "后台分类超时,本轮按无标签落兜底")
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
