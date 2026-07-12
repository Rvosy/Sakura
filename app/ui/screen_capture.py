from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class ScreenCapture:
    """一块屏幕的截图及其全局逻辑坐标。"""

    geometry: QRect
    pixmap: QPixmap
    device_pixel_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.device_pixel_ratio is None:
            object.__setattr__(
                self,
                "device_pixel_ratio",
                self.pixmap.devicePixelRatio() or 1.0,
            )


@dataclass(frozen=True)
class VirtualDesktopCapture:
    """保留各屏幕原生 DPR 的虚拟桌面截图。"""

    screens: tuple[ScreenCapture, ...]
    geometry: QRect

    @classmethod
    def from_pixmap(cls, pixmap: QPixmap, geometry: QRect) -> VirtualDesktopCapture:
        """把旧的单 pixmap 截图包装成兼容的虚拟桌面截图。"""

        return cls((ScreenCapture(QRect(geometry), pixmap),), QRect(geometry))

    def crop(self, global_logical_rect: QRect) -> QPixmap:
        """按各屏 DPR 裁剪并合成全局逻辑区域。"""

        if global_logical_rect.width() <= 0 or global_logical_rect.height() <= 0:
            return QPixmap()

        intersections = [
            (screen, global_logical_rect.intersected(screen.geometry))
            for screen in self.screens
            if screen.geometry.intersects(global_logical_rect)
        ]
        intersections = [(screen, rect) for screen, rect in intersections if not rect.isEmpty()]
        if not intersections:
            return QPixmap()

        # 一张 QPixmap 只能携带一个 DPR。输出使用相交屏幕中的最高 DPR，低 DPR
        # 屏幕内容只在最终合成时缩放，不再错误套用其他屏幕的坐标换算。
        output_dpr = max(
            float(screen.device_pixel_ratio or 1.0) for screen, _rect in intersections
        )
        output_rect = _scale_logical_rect(
            QRect(0, 0, global_logical_rect.width(), global_logical_rect.height()),
            output_dpr,
        )
        result = QPixmap(output_rect.size())
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        for screen, intersection in intersections:
            screen_local = intersection.translated(-screen.geometry.topLeft())
            source_rect = logical_to_device_rect(screen.pixmap, screen_local)
            source_rect = source_rect.intersected(screen.pixmap.rect())
            if source_rect.isEmpty():
                continue

            fragment = screen.pixmap.copy(source_rect)
            fragment.setDevicePixelRatio(1.0)
            output_local = intersection.translated(-global_logical_rect.topLeft())
            target_rect = _scale_logical_rect(output_local, output_dpr)
            painter.drawPixmap(target_rect, fragment, fragment.rect())
        painter.end()
        result.setDevicePixelRatio(output_dpr)
        return result

    def color_at(self, global_logical_pos: QPoint) -> QColor | None:
        """读取全局逻辑坐标处的颜色。"""

        for screen in self.screens:
            if not screen.geometry.contains(global_logical_pos):
                continue
            local = global_logical_pos - screen.geometry.topLeft()
            device = logical_to_device_rect(screen.pixmap, QRect(local, local)).topLeft()
            image = screen.pixmap.toImage()
            if 0 <= device.x() < image.width() and 0 <= device.y() < image.height():
                return image.pixelColor(device)
        return None


def capture_virtual_desktop() -> VirtualDesktopCapture:
    """逐屏截取虚拟桌面，并保留每块屏幕自己的 DPR。"""

    screens = QApplication.screens()
    if not screens:
        raise RuntimeError("无法找到可截图的屏幕。")

    captured: list[ScreenCapture] = []
    virtual_geometry = QRect()
    for screen in screens:
        geometry = screen.geometry()
        virtual_geometry = virtual_geometry.united(geometry)
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            continue
        dpr = screen.devicePixelRatio() or 1.0
        pixmap.setDevicePixelRatio(dpr)
        captured.append(ScreenCapture(QRect(geometry), pixmap, dpr))

    if virtual_geometry.isNull():
        raise RuntimeError("无法获取虚拟桌面区域。")
    if not captured:
        raise RuntimeError("屏幕截图为空，可能被系统权限或显示环境阻止。")
    return VirtualDesktopCapture(tuple(captured), virtual_geometry)


def capture_virtual_desktop_pixmap() -> tuple[QPixmap, QRect]:
    """截取并合成虚拟桌面。

    这是兼容旧调用点的接口。混合 DPI 场景应优先使用 capture_virtual_desktop，
    以免后续裁剪时丢失每块屏幕的 DPR 信息。
    """

    capture = capture_virtual_desktop()
    return capture.crop(capture.geometry), QRect(capture.geometry)


def draw_virtual_desktop_capture(
    painter: QPainter,
    capture: VirtualDesktopCapture,
    origin: QPoint | None = None,
) -> None:
    """在逻辑坐标系中逐屏绘制虚拟桌面截图。"""

    target_origin = origin if origin is not None else QPoint()
    for screen in capture.screens:
        target = QRect(
            screen.geometry.topLeft() - capture.geometry.topLeft() + target_origin,
            screen.geometry.size(),
        )
        painter.drawPixmap(target, screen.pixmap)


def _scale_logical_rect(rect: QRect, dpr: float) -> QRect:
    """缩放矩形边界，避免相邻屏幕因分别舍入宽度产生缝隙。"""

    left = round(rect.x() * dpr)
    top = round(rect.y() * dpr)
    right = round((rect.x() + rect.width()) * dpr)
    bottom = round((rect.y() + rect.height()) * dpr)
    return QRect(left, top, right - left, bottom - top)


def logical_to_device_rect(pixmap: QPixmap, logical_rect: QRect) -> QRect:
    """把逻辑像素矩形换算成 pixmap 的物理像素矩形。"""

    return _scale_logical_rect(logical_rect, pixmap.devicePixelRatio() or 1.0)


def crop_logical_region(
    desktop_pixmap: QPixmap | VirtualDesktopCapture,
    virtual_geometry: QRect,
    global_logical_rect: QRect,
) -> QPixmap:
    """从虚拟桌面截图裁出全局逻辑区域，并保留请求区域的逻辑尺寸。"""

    if isinstance(desktop_pixmap, VirtualDesktopCapture):
        return desktop_pixmap.crop(global_logical_rect)

    capture = VirtualDesktopCapture.from_pixmap(desktop_pixmap, virtual_geometry)
    return capture.crop(global_logical_rect)
