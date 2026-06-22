from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402

from app.ui.manual_screenshot_overlay import ManualScreenshotOverlay  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _half_split_pixmap(logical_w: int, logical_h: int, dpr: float) -> QPixmap:
    """构造一张高 DPI 虚拟桌面图：逻辑左半红、右半蓝，按物理像素分配并设 dpr。"""
    pixmap = QPixmap(round(logical_w * dpr), round(logical_h * dpr))
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)  # painter 按逻辑坐标作画
    painter.fillRect(QRect(0, 0, logical_w // 2, logical_h), QColor("red"))
    painter.fillRect(QRect(logical_w // 2, 0, logical_w - logical_w // 2, logical_h), QColor("blue"))
    painter.end()
    return pixmap


def _dominant_color_name(pixmap: QPixmap) -> str:
    image = pixmap.toImage()
    counts = {"red": 0, "blue": 0, "other": 0}
    red = QColor("red").rgb()
    blue = QColor("blue").rgb()
    for y in range(0, image.height(), max(1, image.height() // 8)):
        for x in range(0, image.width(), max(1, image.width() // 8)):
            rgb = image.pixel(x, y)
            if rgb == red:
                counts["red"] += 1
            elif rgb == blue:
                counts["blue"] += 1
            else:
                counts["other"] += 1
    return max(counts, key=counts.get)


def test_manual_selection_crops_correct_region_under_high_dpi() -> None:
    """框选逻辑右半，应截到物理像素层面的右半（蓝），且分辨率为物理像素。

    回归用例：desktop_pixmap 是「物理像素 + devicePixelRatio」缓冲，copy() 按物理像素取址。
    若按逻辑坐标直接 copy（旧 bug），右半选区会截到横跨红/蓝边界且缩半的错误区域。
    """
    _qt_app_or_skip()
    logical_w, logical_h, dpr = 100, 80, 2.0
    desktop = _half_split_pixmap(logical_w, logical_h, dpr)
    overlay = ManualScreenshotOverlay(desktop, QRect(0, 0, logical_w, logical_h))

    captured: list[QPixmap] = []
    overlay.selected.connect(captured.append)

    # 模拟在逻辑坐标系里框出右半区域。
    overlay.selection_start = QPoint(logical_w // 2, 0)
    overlay.selection_end = QPoint(logical_w, logical_h)
    selection = overlay._selection_rect()

    device_rect = overlay._device_rect(selection)
    assert device_rect == QRect(
        round(selection.x() * dpr),
        round(selection.y() * dpr),
        round(selection.width() * dpr),
        round(selection.height() * dpr),
    )

    cropped = desktop.copy(device_rect)
    # 物理分辨率：逻辑 50x80 在 dpr=2 下应为 100x160 物理像素。
    assert cropped.width() == round(selection.width() * dpr)
    assert cropped.height() == round(selection.height() * dpr)
    # 内容应为纯右半（蓝），不混入左半红色。
    assert _dominant_color_name(cropped) == "blue"

    overlay.deleteLater()
