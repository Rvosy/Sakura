from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# hover 状态轮询间隔：靠回调判断光标是否落在桌宠任一窗口内。
HOVER_POLL_INTERVAL_MS = 180
# 浮现/收起淡入淡出时长，整体保持克制轻盈。
HOVER_ANIM_DURATION_MS = 220
# 发送反馈：输入栏内容做一次轻微"暗-亮"脉冲，每半程时长与最暗透明度。
SEND_PULSE_HALF_MS = 90
SEND_PULSE_DIP_OPACITY = 0.82


class InputBarAnimator(QObject):
    """输入栏卡片 hover 浮现/收起 + 发送反馈脉冲控制器。

    可见性 = hover_active OR pinned。pinned 由「输入框有焦点 / 输入框有文本 / 待确认动作面板」
    决定（不含桌宠讲话），保证用户正在输入或有未完成交互时输入栏不会被收起。

    浮现/收起对**整个输入栏卡片容器**做 QGraphicsOpacityEffect 淡入淡出——连同软件模糊背景一起淡，
    避免内容淡完后背景才突然消失的生硬感（单窗口重构后输入栏为子控件，不能再用 windowOpacity）。
    发送脉冲只作用输入栏内容自身的 opacity effect，与容器级淡入淡出分属父子两层 effect，互不干扰。
    """

    def __init__(
        self,
        input_bar: QWidget,
        input_card: QWidget,
        card_opacity_effect: QGraphicsOpacityEffect,
        is_pinned: Callable[[], bool],
        is_hover_active: Callable[[], bool],
        parent: QObject | None = None,
        before_show: Callable[[], None] | None = None,
        after_show: Callable[[], None] | None = None,
        before_hide: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._input_bar = input_bar
        self._input_card = input_card
        self._card_effect = card_opacity_effect
        self._is_pinned = is_pinned
        self._is_hover_active = is_hover_active
        # 显示前回调：输入栏现身前刷新软件模糊背景截图（截正后方桌面），避免现身后才换背景闪一下。
        self._before_show = before_show
        # 显示/隐藏 hook：用于 macOS 原生 NSVisualEffectView 这类不走 Qt 绘制树的背景层。
        self._after_show = after_show
        self._before_hide = before_hide

        self._hover = False
        self._shown = False
        self._started = False
        # 拖动期间挂起：停轮询并强制隐藏，避免静态模糊背景与移动后的真实桌面对不上而穿帮。
        self._suspended = False
        # 外部强制常显：优先于 hover/pinned，便于临时固定输入栏。
        self._force_visible = False
        # 外部强制隐藏：优先级最高，用于设置窗口等场景避免输入栏挡住其他窗口。
        self._force_hidden = False
        self._anim: QPropertyAnimation | None = None
        self._send_anim: QSequentialAnimationGroup | None = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(HOVER_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll)

    # --- 对外接口 -----------------------------------------------------------
    def start(self) -> None:
        """窗口显示后启动 hover 轮询，并将输入栏落到当前静止态。"""
        if self._started:
            return
        self._started = True
        self._apply_resting_state()
        self._poll_timer.start()

    def set_before_show(self, callback: Callable[[], None] | None) -> None:
        """按当前视觉效果模式动态替换显示前回调。

        - 高斯模糊：_refresh_input_blur_background（截图桌面 + 模糊）
        - 纯色块 / macOS 原生毛玻璃：None（无需截图）
        """
        self._before_show = callback

    def set_after_show(self, callback: Callable[[], None] | None) -> None:
        """替换输入栏 show 后回调。"""
        self._after_show = callback

    def set_before_hide(self, callback: Callable[[], None] | None) -> None:
        """替换输入栏 hide 前回调。"""
        self._before_hide = callback

    def sync(self) -> None:
        """外部 pinned 状态（如待确认动作出现）变化时，立即重算可见性。"""
        self._sync(refresh_hover=True)

    def set_force_visible(self, value: bool) -> None:
        """外部强制常显开关：开启立即现身，关闭后按常规可见性重算。"""
        value = bool(value)
        if value == self._force_visible:
            return
        self._force_visible = value
        if self._started and not self._suspended:
            self._sync(refresh_hover=True)

    def set_force_hidden(self, value: bool) -> None:
        """外部强制隐藏开关：开启后忽略 hover、焦点和 pinned，关闭后恢复常规显隐。"""
        value = bool(value)
        if value == self._force_hidden:
            return
        self._force_hidden = value
        if self._started and not self._suspended:
            self._sync(refresh_hover=True)

    def set_polling_enabled(self, enabled: bool) -> None:
        """临时停/起 hover 轮询（如副窗口打开期间），减少后台无谓的命中测试与重绘。

        仅作用于轮询计时器，不改变当前可见性/动画；未启动或拖动挂起时为空操作，
        以免与 suspend_for_drag 的状态机相互打架。
        """
        if not self._started or self._suspended:
            return
        if enabled:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._poll_timer.stop()

    def suspend_for_drag(self) -> None:
        """拖动开始：停轮询并淡出隐藏输入栏，避免静态模糊背景与移动后的真实桌面穿帮。"""
        self._suspended = True
        self._poll_timer.stop()
        if self._shown:
            # 走淡出动画（而非瞬时隐藏），淡出结束后自动 hide。
            self._shown = False
            self._animate(False)
        else:
            self._maybe_before_hide()
            self._input_card.hide()

    def resume_after_drag(self) -> None:
        """拖动结束：恢复轮询并按当前可见性走淡入/淡出动画重算。

        应可见则经 _animate → before_show 截「新位置」桌面后慢慢淡入现身，否则保持收起。
        输入栏为子控件已随主窗口移动到新位置，无需外部重定位。
        """
        if not self._started:
            self._suspended = False
            return
        self._suspended = False
        self._hover = bool(self._is_hover_active())
        target = self._target_visible()
        self._shown = target
        self._animate(target)
        self._poll_timer.start()

    # --- 内部逻辑 -----------------------------------------------------------
    def _on_poll(self) -> None:
        self._refresh_hover()
        self._sync()

    def _refresh_hover(self) -> None:
        self._hover = bool(self._is_hover_active())

    def _target_visible(self) -> bool:
        if self._force_hidden:
            return False
        return self._force_visible or self._hover or bool(self._is_pinned())

    def _sync(self, *, refresh_hover: bool = False) -> None:
        if self._suspended:
            return
        if refresh_hover:
            self._refresh_hover()
        target = self._target_visible()
        if target == self._shown:
            return
        self._shown = target
        self._animate(target)

    def _animate(self, show: bool) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None
        # 淡入淡出与发送脉冲共用同一个 _card_effect：开始淡入淡出时取消进行中的脉冲，避免两动画相互打架。
        if self._send_anim is not None:
            self._send_anim.stop()
            self._send_anim.deleteLater()
            self._send_anim = None
        if show:
            self._maybe_before_show()
            self._input_card.show()
            self._maybe_after_show()
        else:
            self._maybe_before_hide()
        anim = QPropertyAnimation(self._card_effect, b"opacity")
        anim.setDuration(HOVER_ANIM_DURATION_MS)
        anim.setEndValue(1.0 if show else 0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        if not show:
            anim.finished.connect(self._on_hide_finished)
        anim.start()
        self._anim = anim

    def _on_hide_finished(self) -> None:
        # 淡出结束后再隐藏卡片，避免软件模糊背景残留（若期间又变可见则跳过）。
        if not self._shown:
            self._input_card.hide()

    def _apply_resting_state(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None
        self._shown = self._target_visible()
        if self._shown:
            self._maybe_before_show()
            self._card_effect.setOpacity(1.0)
            self._input_card.show()
            self._maybe_after_show()
        else:
            self._maybe_before_hide()
            self._card_effect.setOpacity(0.0)
            self._input_card.hide()

    def play_send_feedback(self) -> None:
        """发送时让输入栏卡片做一次轻微"暗-亮"脉冲作为反馈（复用 _card_effect，不再单独挂内容 effect）。"""
        # 卡片不可见时不脉冲，避免把隐藏的输入栏短暂闪出。
        if not self._shown:
            return
        if self._send_anim is not None:
            self._send_anim.stop()
            self._send_anim.deleteLater()
        # dim 不设 startValue，从当前透明度起步，避免突跳。
        dim = QPropertyAnimation(self._card_effect, b"opacity")
        dim.setDuration(SEND_PULSE_HALF_MS)
        dim.setEndValue(SEND_PULSE_DIP_OPACITY)
        dim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        restore = QPropertyAnimation(self._card_effect, b"opacity")
        restore.setDuration(SEND_PULSE_HALF_MS)
        restore.setStartValue(SEND_PULSE_DIP_OPACITY)
        restore.setEndValue(1.0)
        restore.setEasingCurve(QEasingCurve.Type.InOutQuad)
        group = QSequentialAnimationGroup(self)
        group.addAnimation(dim)
        group.addAnimation(restore)
        group.start()
        self._send_anim = group

    def _maybe_before_show(self) -> None:
        """显示前回调：在输入栏窗口 show 之前刷新软件模糊背景截图（存在才调）。"""
        if self._before_show is not None:
            self._before_show()

    def _maybe_after_show(self) -> None:
        """显示后回调：用于需要有效原生视图句柄的背景层。"""
        if self._after_show is not None:
            self._after_show()

    def _maybe_before_hide(self) -> None:
        """隐藏前回调：用于移除不随 Qt opacity 一起淡出的原生背景层。"""
        if self._before_hide is not None:
            self._before_hide()
