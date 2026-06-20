from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402

from app.ui.screen_capture import crop_logical_region, logical_to_device_rect  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _make_desktop(logical_w: int, logical_h: int, dpr: float, marker_device: QRect) -> QPixmap:
    """模拟 capture_virtual_desktop_pixmap 的输出：物理像素缓冲 + devicePixelRatio。

    在指定的*物理像素*位置画一个白色标记块，其余透明黑，用于验证裁剪取到的物理区域。
    """
    pixmap = QPixmap(round(logical_w * dpr), round(logical_h * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(QColor(0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    # 直接在物理像素坐标系作画：先把 painter 的 dpr 缩放抵消。
    painter.scale(1.0 / dpr, 1.0 / dpr)
    painter.fillRect(marker_device, QColor(255, 255, 255))
    painter.end()
    return pixmap


def _has_white(pixmap: QPixmap) -> bool:
    image = pixmap.toImage()
    white = QColor(255, 255, 255).rgb()
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixel(x, y) == white:
                return True
    return False


def test_logical_to_device_rect_scales_by_dpr() -> None:
    _qt_app_or_skip()
    pm = QPixmap(3200, 2000)
    pm.setDevicePixelRatio(2.0)
    assert logical_to_device_rect(pm, QRect(1400, 900, 180, 60)) == QRect(2800, 1800, 360, 120)


def test_crop_logical_region_samples_correct_corner_region() -> None:
    """底部右下角的逻辑选区，应裁出对应*物理*像素区域（含其中的标记块）。

    回归用例：标记块放在物理 (2850,1850)，对应逻辑 (1425,925)。
    逻辑选区 (1400,900,180,60) 换算物理为 (2800,1800,360,120)，标记块落在其内。
    旧 bug 直接用逻辑坐标 copy()，会取到物理 (1400,900,180,60)——根本不含标记块。
    """
    _qt_app_or_skip()
    dpr = 2.0
    marker = QRect(2850, 1850, 40, 40)  # 物理像素位置（右下角附近）
    desktop = _make_desktop(1600, 1000, dpr, marker)
    virtual_geometry = QRect(0, 0, 1600, 1000)

    global_rect = QRect(1400, 900, 180, 60)  # 逻辑全局坐标（输入栏在右下角）
    cropped = crop_logical_region(desktop, virtual_geometry, global_rect)

    # 裁出的物理尺寸 = 逻辑尺寸 × dpr。
    assert cropped.width() == 360
    assert cropped.height() == 120
    # devicePixelRatio 被保留，逻辑尺寸≈输入栏尺寸，可直接按逻辑坐标绘制对齐。
    assert cropped.devicePixelRatio() == pytest.approx(dpr)
    # 关键断言：取到了正确物理区域，标记块在其中。
    assert _has_white(cropped)


def test_crop_logical_region_buggy_logical_copy_would_miss_marker() -> None:
    """对照：若按逻辑坐标直接 copy()（旧实现），取到的区域不含标记块。"""
    _qt_app_or_skip()
    marker = QRect(2850, 1850, 40, 40)
    desktop = _make_desktop(1600, 1000, 2.0, marker)
    global_rect = QRect(1400, 900, 180, 60)
    buggy = desktop.copy(global_rect)  # 旧 bug 路径：逻辑矩形被当作物理矩形
    assert not _has_white(buggy)


def test_crop_logical_region_clamps_out_of_bounds() -> None:
    _qt_app_or_skip()
    desktop = _make_desktop(1600, 1000, 2.0, QRect(0, 0, 1, 1))
    virtual_geometry = QRect(0, 0, 1600, 1000)
    # 完全在缓冲之外 → 空 QPixmap。
    assert crop_logical_region(desktop, virtual_geometry, QRect(5000, 5000, 100, 100)).isNull()


@pytest.mark.parametrize(
    ("global_rect", "marker_device", "expected_marker_device"),
    [
        (QRect(-10, -8, 40, 30), QRect(0, 0, 4, 4), QRect(20, 16, 4, 4)),
        (QRect(85, 65, 30, 25), QRect(190, 150, 4, 4), QRect(20, 20, 4, 4)),
    ],
)
def test_crop_logical_region_preserves_requested_size_at_screen_edges(
    global_rect: QRect,
    marker_device: QRect,
    expected_marker_device: QRect,
) -> None:
    """输入栏部分越过屏幕边缘时，裁剪图不能被裁小后重新锚定。

    如果只返回交集小图，后续背景层会把它拉伸到完整输入栏尺寸，表现为越靠四周越错位。
    正确行为是保留请求尺寸，把有效桌面截图放在它相对原请求区域的真实偏移处。
    """
    _qt_app_or_skip()
    dpr = 2.0
    desktop = _make_desktop(100, 80, dpr, marker_device)
    cropped = crop_logical_region(desktop, QRect(0, 0, 100, 80), global_rect)

    assert cropped.width() == round(global_rect.width() * dpr)
    assert cropped.height() == round(global_rect.height() * dpr)
    assert cropped.devicePixelRatio() == pytest.approx(dpr)

    image = cropped.toImage()
    for y in range(expected_marker_device.top(), expected_marker_device.bottom() + 1):
        for x in range(expected_marker_device.left(), expected_marker_device.right() + 1):
            assert image.pixel(x, y) == QColor(255, 255, 255).rgb()
