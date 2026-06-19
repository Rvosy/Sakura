from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPixmap  # noqa: E402

from app.ui.input_blur_background import InputBlurBackground, make_blurred_pixmap  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _solid_pixmap(width: int, height: int) -> QPixmap:
    pix = QPixmap(width, height)
    pix.fill(QColor(120, 180, 220))
    return pix


def test_make_blurred_pixmap_returns_same_size_non_null() -> None:
    _qt_app_or_skip()
    src = _solid_pixmap(160, 52)
    result = make_blurred_pixmap(src, radius=8.0, downscale=4)
    assert not result.isNull()
    assert result.size() == src.size()


def test_make_blurred_pixmap_handles_null_source() -> None:
    _qt_app_or_skip()
    # 空输入应降级返回（不抛、仍是 QPixmap）。
    result = make_blurred_pixmap(QPixmap(), radius=8.0, downscale=4)
    assert result.isNull()


def test_input_blur_background_paint_with_pixmap_does_not_raise() -> None:
    _qt_app_or_skip()
    widget = InputBlurBackground(corner_radius=22.0)
    widget.resize(160, 52)
    widget.set_tint(QColor(255, 255, 255, 40))
    widget.set_blurred_pixmap(_solid_pixmap(160, 52))
    pixmap = widget.grab()  # 触发 paintEvent
    assert not pixmap.isNull()
    widget.deleteLater()


def test_input_blur_background_uses_configured_shadow_overlay() -> None:
    _qt_app_or_skip()
    widget = InputBlurBackground(corner_radius=0.0)
    widget.resize(120, 40)
    widget.set_shadow_overlay(QColor(80, 0, 0, 128))
    widget.set_tint(QColor(255, 255, 255, 0))
    widget.set_blurred_pixmap(_solid_pixmap(120, 40))

    image = widget.grab().toImage()
    center = image.pixelColor(60, 20)

    assert center.red() < 120
    assert center.green() < 180
    assert center.blue() < 220
    assert center.red() > center.green()
    widget.deleteLater()


def test_input_blur_background_paint_without_pixmap_uses_tint() -> None:
    _qt_app_or_skip()
    widget = InputBlurBackground()
    widget.resize(120, 40)
    # 没有截图时也应能绘制（tint 兜底），不抛。
    pixmap = widget.grab()
    assert not pixmap.isNull()
    widget.deleteLater()
