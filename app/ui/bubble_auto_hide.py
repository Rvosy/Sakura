from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# hover 轮询间隔：判断光标是否停在桌宠区域以暂停（重置）倒计时。
HOVER_POLL_INTERVAL_MS = 300
# 气泡淡入淡出时长。
FADE_DURATION_MS = 220


class BubbleAutoHideController(QObject):
    """对话气泡无操作自动隐藏控制器（悬停暂停式）。

    行为：桌宠说完话后开始倒计时；倒计时期间鼠标停在桌宠/气泡区域则不断重置（等效暂停），
    移开后跑完延时即淡出隐藏气泡。隐藏后单击桌宠唤回；说话/新台词时保持显示、停止倒计时。
    关闭开关时控制器空转（气泡常显）。淡入淡出作用于气泡卡片容器的 QGraphicsOpacityEffect
    （单窗口重构后气泡为子控件，不能再用 windowOpacity），与内容脉冲动画分属父子两层 effect，互不干扰。
    """

    def __init__(
        self,
        bubble_card: QWidget,
        opacity_effect: QGraphicsOpacityEffect,
        is_cursor_in_region: Callable[[], bool],
        *,
        enabled: bool,
        delay_seconds: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bubble_card = bubble_card
        self._opacity_effect = opacity_effect
        self._is_cursor_in_region = is_cursor_in_region
        self._enabled = bool(enabled)
        self._delay_ms = max(1, int(delay_seconds)) * 1000
        self._hidden = False
        self._settled = False
        self._fade_anim: QPropertyAnimation | None = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timeout)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(HOVER_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll)

    # --- 对外接口 -----------------------------------------------------------
    def start(self) -> None:
        """窗口就绪后启动：把当前已显示的气泡纳入自动隐藏（视作已说完，开始倒计时）。"""
        self._settled = True
        if self._enabled:
            self._start_countdown()

    def set_settings(self, *, enabled: bool, delay_seconds: int) -> None:
        """更新开关与时长：关闭时停表并确保气泡可见；启用且已说完则重新计时。"""
        self._enabled = bool(enabled)
        self._delay_ms = max(1, int(delay_seconds)) * 1000
        if not self._enabled:
            self._hide_timer.stop()
            self._poll_timer.stop()
            self._reveal()
        elif self._settled:
            self._start_countdown()

    def notify_speaking(self) -> None:
        """有新台词/正在说话：保持显示并停倒计时（说话期间不隐藏）。"""
        self._settled = False
        self._hide_timer.stop()
        self._poll_timer.stop()
        if self._hidden:
            self._reveal()

    def notify_settled(self) -> None:
        """说完话（reply 完成）：开始无操作倒计时。"""
        self._settled = True
        self._start_countdown()

    def handle_pet_clicked(self) -> None:
        """单击桌宠（非拖动）：唤回被自动隐藏的气泡并重新计时。"""
        if self._hidden:
            self._reveal()
            if self._enabled and self._settled:
                self._start_countdown()

    @property
    def is_hidden(self) -> bool:
        return self._hidden

    # --- 内部逻辑 -----------------------------------------------------------
    def _start_countdown(self) -> None:
        if not self._enabled:
            return
        self._hide_timer.start(self._delay_ms)
        self._poll_timer.start()

    def _on_poll(self) -> None:
        # 鼠标停在桌宠/气泡区域 → 重置倒计时（等效暂停）。
        if self._is_cursor_in_region():
            self._hide_timer.start(self._delay_ms)

    def _on_hide_timeout(self) -> None:
        if not self._enabled or self._hidden:
            return
        # 超时瞬间鼠标又回到区域内，则继续等待而不隐藏。
        if self._is_cursor_in_region():
            self._hide_timer.start(self._delay_ms)
            return
        self._poll_timer.stop()
        self._hide()

    def _hide(self) -> None:
        self._hidden = True
        card = self._bubble_card
        if card is None or not card.isVisible():
            return
        anim = self._make_fade(self._opacity_effect.opacity(), 0.0)
        anim.finished.connect(self._on_fade_out_finished)
        anim.start()
        self._fade_anim = anim

    def _on_fade_out_finished(self) -> None:
        if self._hidden and self._bubble_card is not None:
            self._bubble_card.hide()

    def _reveal(self) -> None:
        self._hidden = False
        card = self._bubble_card
        if card is None:
            return
        if not card.isVisible():
            self._opacity_effect.setOpacity(0.0)
            card.show()
        anim = self._make_fade(self._opacity_effect.opacity(), 1.0)
        anim.start()
        self._fade_anim = anim

    def _make_fade(self, start: float, end: float) -> QPropertyAnimation:
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim.deleteLater()
            self._fade_anim = None
        anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        anim.setDuration(FADE_DURATION_MS)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        return anim
