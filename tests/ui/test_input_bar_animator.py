from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

from app.ui.input_bar_animator import InputBarAnimator  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def test_before_show_runs_before_card_show() -> None:
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

    _qt_app_or_skip()
    bar = QWidget()
    card = QWidget()
    effect = QGraphicsOpacityEffect(card)
    visible_at_before_show: list[bool] = []

    animator = InputBarAnimator(
        bar,
        card,
        effect,
        is_pinned=lambda: True,  # pinned → 静止态应显示
        is_hover_active=lambda: False,
        before_show=lambda: visible_at_before_show.append(card.isVisible()),
    )
    animator.start()

    # before_show 应在 show 之前被调用一次：调用时卡片尚未显示。
    assert visible_at_before_show == [False]
    assert card.isVisible()

    bar.deleteLater()
    card.deleteLater()


def test_suspend_for_drag_hides_and_blocks_sync() -> None:
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

    _qt_app_or_skip()
    bar = QWidget()
    card = QWidget()
    effect = QGraphicsOpacityEffect(card)
    animator = InputBarAnimator(
        bar,
        card,
        effect,
        is_pinned=lambda: True,
        is_hover_active=lambda: False,
    )
    animator.start()
    assert card.isVisible()

    animator.suspend_for_drag()
    # 挂起：标记为不显示并启动淡出（动画异步，卡片稍后才真正 hide）。
    assert animator._shown is False

    # 挂起期间即便 pinned 也不应被 sync 重新拉出。
    animator.sync()
    assert animator._shown is False

    # 恢复后按可见性重算：pinned → 淡入显示（show 立即调用，淡入动画异步）。
    animator.resume_after_drag()
    assert animator._shown is True
    assert card.isVisible()

    bar.deleteLater()
    card.deleteLater()
