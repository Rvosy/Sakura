from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEventLoop, QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from app.ui.screen_capture import (
    VirtualDesktopCapture,
    capture_virtual_desktop,
    logical_to_device_rect,
)

COLOR_PICKER_SAMPLE_SIZE = 17
COLOR_PICKER_PREVIEW_SIZE = 148
COLOR_PICKER_REFRESH_INTERVAL_MS = 33


def sample_desktop_hex_color(
    desktop_pixmap: QPixmap | VirtualDesktopCapture,
    virtual_geometry: QRect,
    global_pos: QPoint,
) -> str | None:
    """Return #rrggbb for a logical global desktop point."""

    if isinstance(desktop_pixmap, VirtualDesktopCapture):
        color = desktop_pixmap.color_at(global_pos)
        return color.name() if color is not None else None

    local = QPoint(global_pos) - virtual_geometry.topLeft()
    if not QRect(QPoint(0, 0), virtual_geometry.size()).contains(local):
        return None
    device_rect = logical_to_device_rect(desktop_pixmap, QRect(local, local))
    x = device_rect.x()
    y = device_rect.y()
    image = desktop_pixmap.toImage()
    if x < 0 or y < 0 or x >= image.width() or y >= image.height():
        return None
    return image.pixelColor(x, y).name()


def sample_pixmap_hex_color(pixmap: QPixmap, logical_pos: QPoint) -> str | None:
    device_rect = logical_to_device_rect(pixmap, QRect(logical_pos, logical_pos))
    x = device_rect.x()
    y = device_rect.y()
    image = pixmap.toImage()
    if x < 0 or y < 0 or x >= image.width() or y >= image.height():
        return None
    return image.pixelColor(x, y).name()


def capture_live_screen_region(global_rect: QRect) -> tuple[QPixmap, QRect] | None:
    if global_rect.isEmpty():
        return None
    app = QApplication.instance()
    if app is None:
        return None
    screen = QApplication.screenAt(global_rect.center())
    if screen is None:
        screen = next(
            (candidate for candidate in QApplication.screens() if candidate.geometry().intersects(global_rect)),
            None,
        )
    if screen is None:
        return None
    capture_rect = QRect(global_rect).intersected(screen.geometry())
    if capture_rect.isEmpty():
        return None
    local_rect = capture_rect.translated(-screen.geometry().topLeft())
    pixmap = screen.grabWindow(
        0,
        local_rect.x(),
        local_rect.y(),
        local_rect.width(),
        local_rect.height(),
    )
    if pixmap.isNull():
        return None
    return pixmap, capture_rect


class ScreenColorPickerOverlay(QWidget):
    picked = Signal(str)
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
        self._sample_cache: tuple[QPixmap, QRect, str | None] | None = None
        self._sample_refresh_timer = QTimer(self)
        self._sample_refresh_timer.setSingleShot(True)
        self._sample_refresh_timer.setInterval(COLOR_PICKER_REFRESH_INTERVAL_MS)
        self._sample_refresh_timer.timeout.connect(self._refresh_sample_cache_and_update)
        self._override_cursor = False
        cursor_pos = QCursor.pos() - self.virtual_geometry.topLeft()
        self.cursor_pos = QPoint(
            min(max(0, cursor_pos.x()), max(0, self.virtual_geometry.width() - 1)),
            min(max(0, cursor_pos.y()), max(0, self.virtual_geometry.height() - 1)),
        )
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setGeometry(self.virtual_geometry)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        # Fully transparent layered windows can become click-through on Windows.
        painter.fillRect(event.rect(), QColor(0, 0, 0, 1))
        self._draw_preview(painter)
        painter.end()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._override_cursor:
            QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
            self._override_cursor = True
        self._queue_sample_refresh()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._restore_cursor()
        super().closeEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.cursor_pos = event.position().toPoint()
        self._queue_sample_refresh()
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.cursor_pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        color = self._current_color()
        if color is None:
            self._cancel()
            return
        self.picked.emit(color)
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def _current_color(self) -> str | None:
        self._refresh_sample_cache()
        if self._sample_cache is not None and self._sample_cache[2]:
            return self._sample_cache[2]
        return self._fallback_current_color()

    def _fallback_current_color(self) -> str | None:
        return sample_desktop_hex_color(
            self.desktop_capture,
            self.virtual_geometry,
            self.virtual_geometry.topLeft() + self.cursor_pos,
        )

    def _sample_rect(self) -> QRect:
        half = COLOR_PICKER_SAMPLE_SIZE // 2
        return QRect(
            self.cursor_pos.x() - half,
            self.cursor_pos.y() - half,
            COLOR_PICKER_SAMPLE_SIZE,
            COLOR_PICKER_SAMPLE_SIZE,
        ).intersected(self.rect())

    def _sample_source(self, source: QRect) -> tuple[QPixmap, QRect, str | None] | None:
        global_rect = QRect(source).translated(self.virtual_geometry.topLeft())
        try:
            live = capture_live_screen_region(global_rect)
        except Exception:  # noqa: BLE001 - screen capture can fail under desktop restrictions.
            live = None
        if live is not None:
            pixmap, captured_rect = live
            color = sample_pixmap_hex_color(
                pixmap,
                self.virtual_geometry.topLeft() + self.cursor_pos - captured_rect.topLeft(),
            )
            return pixmap, captured_rect, color

        return self._fallback_sample_source(source)

    def _fallback_sample_source(self, source: QRect) -> tuple[QPixmap, QRect, str | None]:
        global_rect = QRect(source).translated(self.virtual_geometry.topLeft())
        pixmap = self.desktop_capture.crop(global_rect)
        return pixmap, global_rect, self._fallback_current_color()

    def _queue_sample_refresh(self) -> None:
        if self._sample_refresh_timer.isActive():
            return
        self._sample_refresh_timer.start()

    def _refresh_sample_cache_and_update(self) -> None:
        self._refresh_sample_cache()
        self.update()

    def _refresh_sample_cache(self) -> None:
        source = self._sample_rect()
        self._sample_cache = self._sample_source(source) if not source.isEmpty() else None

    def _draw_preview(self, painter: QPainter) -> None:
        source = self._sample_rect()
        if source.isEmpty():
            return

        preview = QRect(
            self.cursor_pos.x() + 22,
            self.cursor_pos.y() + 22,
            COLOR_PICKER_PREVIEW_SIZE,
            COLOR_PICKER_PREVIEW_SIZE,
        )
        if preview.right() > self.rect().right():
            preview.moveRight(self.cursor_pos.x() - 22)
        if preview.bottom() > self.rect().bottom():
            preview.moveBottom(self.cursor_pos.y() - 22)
        preview = preview.intersected(self.rect())
        if preview.width() < 60 or preview.height() < 60:
            return

        sample = self._sample_cache or self._fallback_sample_source(source)
        if sample is None:
            return
        source_pixmap, _captured_rect, color = sample
        color = color or "#000000"

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(preview.adjusted(-5, -5, 5, 29), QColor(20, 20, 24, 220))
        source_pixmap.setDevicePixelRatio(1.0)
        scaled = source_pixmap.scaled(
            preview.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawPixmap(preview.topLeft(), scaled)

        center = preview.center()
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawLine(center.x(), preview.top(), center.x(), preview.bottom())
        painter.drawLine(preview.left(), center.y(), preview.right(), center.y())
        painter.setPen(QPen(QColor("#111111"), 1))
        painter.drawRect(preview.adjusted(0, 0, -1, -1))

        label_rect = QRect(preview.left(), preview.bottom() + 6, preview.width(), 22)
        painter.fillRect(label_rect, QColor(20, 20, 24, 235))
        painter.fillRect(QRect(label_rect.left() + 6, label_rect.top() + 5, 16, 12), QColor(color))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(label_rect.adjusted(30, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, color)

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()

    def _restore_cursor(self) -> None:
        if self._override_cursor:
            QApplication.restoreOverrideCursor()
            self._override_cursor = False


def pick_screen_color() -> str | None:
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("无法启动取色器：Qt 应用尚未初始化。")

    desktop_capture = capture_virtual_desktop()
    overlay = ScreenColorPickerOverlay(desktop_capture)
    loop = QEventLoop()
    result: dict[str, str] = {}

    def finish(color: str | None = None) -> None:
        if color:
            result["color"] = color
        if loop.isRunning():
            loop.quit()

    overlay.picked.connect(lambda color: finish(str(color)))
    overlay.cancelled.connect(lambda: finish())
    overlay.destroyed.connect(lambda _obj=None: finish())
    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    _force_topmost(overlay)
    overlay.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
    loop.exec()
    if overlay.isVisible():
        overlay.close()
    return result.get("color")


def _force_topmost(widget: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetWindowPos(
            int(widget.winId()),
            -1,  # HWND_TOPMOST
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0040,  # NOSIZE | NOMOVE | SHOWWINDOW
        )
    except Exception:  # noqa: BLE001 - best-effort z-order nudge.
        return
