from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication


def capture_virtual_desktop_pixmap() -> tuple[QPixmap, QRect]:
    """截取所有屏幕合并后的虚拟桌面。"""

    screens = QApplication.screens()
    if not screens:
        raise RuntimeError("无法找到可截图的屏幕。")

    virtual_geometry = QRect()
    for screen in screens:
        virtual_geometry = virtual_geometry.united(screen.geometry())
    if virtual_geometry.isNull():
        raise RuntimeError("无法获取虚拟桌面区域。")

    # 按物理像素分配缓冲区，避免高 DPI 缩放导致截图模糊。
    # 注意：返回的 QPixmap 是「物理像素缓冲 + devicePixelRatio」。
    #   - QPainter / drawPixmap(target, pixmap) 这类按逻辑坐标绘制的接口无需改动；
    #   - 但 copy()、rect()、width()/height()、drawPixmap(target, pixmap, source) 的
    #     源矩形都以物理像素为单位，下游若按逻辑坐标裁剪须先乘 devicePixelRatio，
    #     否则裁出的区域会缩半并随坐标增大向左上偏移（屏幕边缘尤其明显）。
    max_dpr = max(s.devicePixelRatio() for s in screens)
    phys_w = round(virtual_geometry.width() * max_dpr)
    phys_h = round(virtual_geometry.height() * max_dpr)
    desktop_pixmap = QPixmap(phys_w, phys_h)
    desktop_pixmap.setDevicePixelRatio(max_dpr)
    desktop_pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(desktop_pixmap)
    captured_count = 0
    for screen in screens:
        screen_pixmap = screen.grabWindow(0)
        if screen_pixmap.isNull():
            continue
        target_rect = QRect(
            screen.geometry().topLeft() - virtual_geometry.topLeft(),
            screen.geometry().size(),
        )
        painter.drawPixmap(target_rect, screen_pixmap)
        captured_count += 1
    painter.end()

    if captured_count == 0:
        raise RuntimeError("屏幕截图为空，可能被系统权限或显示环境阻止。")
    return desktop_pixmap, virtual_geometry


def logical_to_device_rect(pixmap: QPixmap, logical_rect: QRect) -> QRect:
    """把逻辑像素矩形换算成 pixmap 的物理像素矩形。

    capture_virtual_desktop_pixmap 返回的是「物理像素缓冲 + devicePixelRatio」的 QPixmap：
    copy()/rect()/width()/height() 及 drawPixmap 源矩形都以物理像素计；而上层（mapToGlobal、
    screen.geometry()、鼠标事件）给的是逻辑像素。二者相差一个 devicePixelRatio，必须显式换算，
    否则裁剪区域会缩放并随坐标增大而偏移（屏幕边缘/角落尤其明显）。
    """
    dpr = pixmap.devicePixelRatio() or 1.0
    return QRect(
        round(logical_rect.x() * dpr),
        round(logical_rect.y() * dpr),
        round(logical_rect.width() * dpr),
        round(logical_rect.height() * dpr),
    )


def crop_logical_region(
    desktop_pixmap: QPixmap,
    virtual_geometry: QRect,
    global_logical_rect: QRect,
) -> QPixmap:
    """从虚拟桌面物理像素缓冲里裁出 global_logical_rect（逻辑全局坐标）对应区域。

    返回的子图保留 devicePixelRatio，其逻辑尺寸≈global_logical_rect，可直接按逻辑坐标绘制对齐。
    若请求区域有一部分超出桌面，会保留原始输出尺寸并把有效截图放在它相对请求区域的真实偏移处；
    这样贴回同尺寸窗口时不会因边缘裁剪而重新锚定、拉伸错位。与缓冲完全无交集时返回空 QPixmap。
    """
    offset = global_logical_rect.translated(
        -virtual_geometry.x(), -virtual_geometry.y()
    )
    requested_device_rect = logical_to_device_rect(desktop_pixmap, offset)
    if requested_device_rect.width() <= 0 or requested_device_rect.height() <= 0:
        return QPixmap()

    device_crop = requested_device_rect.intersected(desktop_pixmap.rect())
    if device_crop.isEmpty():
        return QPixmap()
    if device_crop == requested_device_rect:
        return desktop_pixmap.copy(device_crop)

    dpr = desktop_pixmap.devicePixelRatio() or 1.0
    result = QPixmap(
        requested_device_rect.width(),
        requested_device_rect.height(),
    )
    result.fill(Qt.GlobalColor.transparent)

    cropped = desktop_pixmap.copy(device_crop)
    cropped.setDevicePixelRatio(1.0)
    painter = QPainter(result)
    painter.drawPixmap(device_crop.topLeft() - requested_device_rect.topLeft(), cropped)
    painter.end()
    result.setDevicePixelRatio(dpr)
    return result
