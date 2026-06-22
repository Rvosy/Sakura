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


def test_make_blurred_pixmap_preserves_position_under_high_dpi() -> None:
    """高 DPI（dpr>1）截图模糊后，亮区不能被缩到一半并漂到左上角。

    回归用例：旧实现把带 devicePixelRatio 的 src 交给 scaled()/QGraphicsScene，
    会把内容缩到 1/dpr 并锚定左上角，导致输入栏模糊背景随位置漂移错位。
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter

    _qt_app_or_skip()
    dpr = 2.0
    src = QPixmap(200, 200)
    src.setDevicePixelRatio(dpr)
    src.fill(QColor(0, 0, 0))
    painter = QPainter(src)
    painter.scale(1.0 / dpr, 1.0 / dpr)  # 在物理像素坐标作画
    painter.fillRect(QRect(150, 150, 50, 50), QColor(255, 255, 255))  # 右下角物理象限
    painter.end()

    out = make_blurred_pixmap(src, radius=4.0, downscale=2)
    assert out.size() == src.size()
    assert out.devicePixelRatio() == pytest.approx(dpr)
    assert src.devicePixelRatio() == pytest.approx(dpr)  # 不得篡改调用方 src

    image = out.toImage()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if (image.pixel(x, y) & 0xFF) > 120:  # 亮（含模糊扩散）
                xs.append(x)
            if (image.pixel(x, y) & 0xFF) > 120:
                ys.append(y)
    assert xs and ys, "模糊后应仍有亮区"
    # 亮区质心应落在右下半区（物理像素 > 100），而非被压到左上。
    assert sum(xs) / len(xs) > 100
    assert sum(ys) / len(ys) > 100


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
