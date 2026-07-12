from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Signal, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from app.ui.screen_capture import (
    VirtualDesktopCapture,
    draw_virtual_desktop_capture,
    logical_to_device_rect,
)


MANUAL_SCREENSHOT_MIN_SIZE = 8


class ManualScreenshotOverlay(QWidget):
    """全屏框选覆盖层，用于生成手动截图上下文。"""

    selected = Signal(object)
    cancelled = Signal()

    def __init__(
        self,
        desktop_pixmap: QPixmap | VirtualDesktopCapture,
        virtual_geometry: QRect | None = None,
    ) -> None:
        super().__init__(None)
        if isinstance(desktop_pixmap, VirtualDesktopCapture):
            self.desktop_capture = desktop_pixmap
            self.desktop_pixmap = (
                desktop_pixmap.screens[0].pixmap if len(desktop_pixmap.screens) == 1 else QPixmap()
            )
            self.virtual_geometry = QRect(desktop_pixmap.geometry)
        else:
            if virtual_geometry is None:
                raise TypeError("单张桌面截图必须提供 virtual_geometry。")
            self.desktop_pixmap = desktop_pixmap
            self.virtual_geometry = QRect(virtual_geometry)
            self.desktop_capture = VirtualDesktopCapture.from_pixmap(
                desktop_pixmap, self.virtual_geometry
            )
        self.selection_start: QPoint | None = None
        self.selection_end: QPoint | None = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(self.virtual_geometry)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        draw_virtual_desktop_capture(painter, self.desktop_capture)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 95))

        selection = self._selection_rect()
        if not selection.isNull():
            selected = self._crop_selection(selection)
            painter.drawPixmap(selection, selected)
            painter.fillRect(selection, QColor(255, 255, 255, 28))
            painter.setPen(QColor(74, 170, 214, 245))
            painter.drawRect(selection.adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.selection_start = event.position().toPoint()
        self.selection_end = self.selection_start
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.selection_start is None:
            return
        self.selection_end = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.selection_start is None:
            return
        self.selection_end = event.position().toPoint()
        selection = self._selection_rect()
        if (
            selection.width() < MANUAL_SCREENSHOT_MIN_SIZE
            or selection.height() < MANUAL_SCREENSHOT_MIN_SIZE
        ):
            self._cancel()
            return
        self.selected.emit(self._crop_selection(selection))
        self.close()

    def _crop_selection(self, rect: QRect) -> QPixmap:
        global_rect = QRect(rect).translated(self.virtual_geometry.topLeft())
        return self.desktop_capture.crop(global_rect)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def _device_rect(self, rect: QRect) -> QRect:
        """把覆盖层逻辑坐标矩形换算成 desktop_pixmap 的物理像素矩形。

        高 DPI 下 desktop_pixmap 按物理像素分配并设了 devicePixelRatio，
        copy()/drawPixmap 源矩形都以物理像素为单位，须乘以该比例。
        """
        if len(self.desktop_capture.screens) != 1:
            raise ValueError("混合 DPI 桌面没有单一的设备像素矩形。")
        screen = self.desktop_capture.screens[0]
        global_rect = QRect(rect).translated(self.virtual_geometry.topLeft())
        screen_local = global_rect.translated(-screen.geometry.topLeft())
        return logical_to_device_rect(screen.pixmap, screen_local)

    def _selection_rect(self) -> QRect:
        if self.selection_start is None or self.selection_end is None:
            return QRect()
        return QRect(self.selection_start, self.selection_end).normalized().intersected(self.rect())

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()
