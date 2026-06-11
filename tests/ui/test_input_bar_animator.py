from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

from app.ui.input_bar_animator import InputBarAnimator  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def test_before_show_runs_before_window_show() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    bar = QWidget()
    window = QWidget()
    visible_at_before_show: list[bool] = []

    animator = InputBarAnimator(
        bar,
        window,
        is_pinned=lambda: True,  # pinned → 静止态应显示
        is_hover_active=lambda: False,
        before_show=lambda: visible_at_before_show.append(window.isVisible()),
    )
    animator.start()

    # before_show 应在 show 之前被调用一次：调用时窗口尚未显示。
    assert visible_at_before_show == [False]
    assert window.isVisible()

    bar.deleteLater()
    window.deleteLater()


def test_show_paths_raise_window_above_portrait() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    bar = QWidget()

    class RaiseTrackingWindow(QWidget):
        raise_count = 0

        def raise_(self) -> None:  # noqa: N802
            self.raise_count += 1
            super().raise_()

    window = RaiseTrackingWindow()
    animator = InputBarAnimator(
        bar,
        window,
        is_pinned=lambda: True,
        is_hover_active=lambda: False,
    )
    # macOS 上 Qt.Tool 子窗口 show() 不会自动浮到立绘之上，两条显示路径都必须 raise。
    # 静止态显示路径：start → _apply_resting_state。
    animator.start()
    assert window.isVisible()
    assert window.raise_count == 1

    # 淡入显示路径：拖动挂起后恢复 → _animate(True)。
    animator.suspend_for_drag()
    window.hide()  # 模拟淡出动画结束后的真正隐藏
    window.raise_count = 0
    animator.resume_after_drag()
    assert window.isVisible()
    assert window.raise_count == 1

    bar.deleteLater()
    window.deleteLater()


def test_suspend_for_drag_hides_and_blocks_sync() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    bar = QWidget()
    window = QWidget()
    animator = InputBarAnimator(
        bar,
        window,
        is_pinned=lambda: True,
        is_hover_active=lambda: False,
    )
    animator.start()
    assert window.isVisible()

    animator.suspend_for_drag()
    # 挂起：标记为不显示并启动淡出（动画异步，窗口稍后才真正 hide）。
    assert animator._shown is False

    # 挂起期间即便 pinned 也不应被 sync 重新拉出。
    animator.sync()
    assert animator._shown is False

    # 恢复后按可见性重算：pinned → 淡入显示（show 立即调用，淡入动画异步）。
    animator.resume_after_drag()
    assert animator._shown is True
    assert window.isVisible()

    bar.deleteLater()
    window.deleteLater()
