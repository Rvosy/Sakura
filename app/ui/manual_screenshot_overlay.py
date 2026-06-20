from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Signal, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from app.ui.screen_capture import logical_to_device_rect


MANUAL_SCREENSHOT_MIN_SIZE = 8


class ManualScreenshotOverlay(QWidget):
    """全屏框选覆盖层，用于生成手动截图上下文。"""

    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, desktop_pixmap: QPixmap, virtual_geometry: QRect) -> None:
        super().__init__(None)
        self.desktop_pixmap = desktop_pixmap
        self.virtual_geometry = QRect(virtual_geometry)
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
        painter.drawPixmap(self.rect(), self.desktop_pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 95))

        selection = self._selection_rect()
        if not selection.isNull():
            # 覆盖层按逻辑坐标布局，但 desktop_pixmap 是物理像素缓冲，
            # drawPixmap 的源矩形按物理像素取址，故须把逻辑选区换算成物理像素。
            painter.drawPixmap(selection, self.desktop_pixmap, self._device_rect(selection))
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
        # copy() 按物理像素取址，须用换算后的物理选区，否则截到的是缩半且左上偏移的错误区域。
        self.selected.emit(self.desktop_pixmap.copy(self._device_rect(selection)))
        self.close()

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
        return logical_to_device_rect(self.desktop_pixmap, rect)

    def _selection_rect(self) -> QRect:
        if self.selection_start is None or self.selection_end is None:
            return QRect()
        return QRect(self.selection_start, self.selection_end).normalized().intersected(self.rect())

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()
