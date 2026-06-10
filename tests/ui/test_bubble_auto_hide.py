from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

from app.ui.bubble_auto_hide import BubbleAutoHideController  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _make(window, in_region, *, enabled=True, delay_seconds=1):  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QGraphicsOpacityEffect

    effect = QGraphicsOpacityEffect(window)
    return BubbleAutoHideController(
        window,
        effect,
        lambda: in_region[0],
        enabled=enabled,
        delay_seconds=delay_seconds,
    )


def test_disabled_does_not_start_countdown() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    window = QWidget()
    window.show()
    controller = _make(window, [False], enabled=False)
    controller.notify_settled()
    assert not controller._hide_timer.isActive()
    window.deleteLater()


def test_settled_starts_countdown_and_timeout_hides() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    window = QWidget()
    window.show()
    controller = _make(window, [False])
    controller.notify_settled()
    assert controller._hide_timer.isActive()
    # 直接触发超时回调（不等真实计时）。鼠标不在区域 → 进入隐藏态。
    controller._on_hide_timeout()
    assert controller.is_hidden is True
    window.deleteLater()


def test_hover_at_timeout_keeps_visible_and_restarts() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    window = QWidget()
    window.show()
    controller = _make(window, [True])  # 鼠标停在区域内
    controller.notify_settled()
    controller._on_hide_timeout()
    # 悬停暂停式：超时瞬间仍在区域 → 不隐藏并重启倒计时。
    assert controller.is_hidden is False
    assert controller._hide_timer.isActive()
    window.deleteLater()


def test_speaking_stops_and_reveals() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    window = QWidget()
    window.show()
    controller = _make(window, [False])
    controller.notify_settled()
    controller._on_hide_timeout()
    assert controller.is_hidden
    controller.notify_speaking()
    assert controller.is_hidden is False
    assert not controller._hide_timer.isActive()
    window.deleteLater()


def test_pet_click_reveals_when_hidden() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    window = QWidget()
    window.show()
    controller = _make(window, [False])
    controller.notify_settled()
    controller._on_hide_timeout()
    assert controller.is_hidden
    controller.handle_pet_clicked()
    assert controller.is_hidden is False
    window.deleteLater()


def test_set_settings_disable_reveals_and_stops() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    window = QWidget()
    window.show()
    controller = _make(window, [False])
    controller.notify_settled()
    controller._on_hide_timeout()
    assert controller.is_hidden
    # 关闭自动隐藏：应停表并唤回气泡。
    controller.set_settings(enabled=False, delay_seconds=5)
    assert controller.is_hidden is False
    assert not controller._hide_timer.isActive()
    window.deleteLater()
