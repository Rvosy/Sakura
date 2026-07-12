from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402

from app.ui.screen_color_picker import (  # noqa: E402
    COLOR_PICKER_REFRESH_INTERVAL_MS,
    ScreenColorPickerOverlay,
    sample_pixmap_hex_color,
    sample_desktop_hex_color,
)
from app.ui.screen_capture import ScreenCapture, VirtualDesktopCapture  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _desktop(logical_w: int, logical_h: int, dpr: float, marker: QRect, color: str) -> QPixmap:
    pixmap = QPixmap(round(logical_w * dpr), round(logical_h * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(QColor("#000000"))
    painter = QPainter(pixmap)
    painter.scale(1.0 / dpr, 1.0 / dpr)
    painter.fillRect(marker, QColor(color))
    painter.end()
    return pixmap


def test_screen_color_picker_samples_high_dpi_physical_pixel() -> None:
    _qt_app_or_skip()
    desktop = _desktop(120, 80, 2.0, QRect(140, 60, 4, 4), "#12ab34")

    assert sample_desktop_hex_color(desktop, QRect(0, 0, 120, 80), QPoint(70, 30)) == "#12ab34"


def test_screen_color_picker_samples_negative_virtual_geometry() -> None:
    _qt_app_or_skip()
    dpr = 1.5
    desktop = _desktop(200, 100, dpr, QRect(round(75 * dpr), round(40 * dpr), 3, 3), "#3366cc")

    assert (
        sample_desktop_hex_color(
            desktop,
            QRect(-100, -50, 200, 100),
            QPoint(-25, -10),
        )
        == "#3366cc"
    )


def test_screen_color_picker_samples_secondary_screen_with_its_own_dpr() -> None:
    _qt_app_or_skip()
    primary = _desktop(100, 80, 1.5, QRect(0, 0, 1, 1), "#000000")
    secondary = _desktop(100, 80, 1.0, QRect(40, 30, 2, 2), "#55aaee")
    capture = VirtualDesktopCapture(
        (
            ScreenCapture(QRect(0, 0, 100, 80), primary),
            ScreenCapture(QRect(100, 0, 100, 80), secondary),
        ),
        QRect(0, 0, 200, 80),
    )

    assert sample_desktop_hex_color(capture, capture.geometry, QPoint(140, 30)) == "#55aaee"


def test_screen_color_picker_cancel_does_not_pick() -> None:
    _qt_app_or_skip()
    desktop = _desktop(20, 20, 1.0, QRect(1, 1, 2, 2), "#ffffff")
    overlay = ScreenColorPickerOverlay(desktop, QRect(0, 0, 20, 20))
    picked: list[str] = []
    cancelled: list[bool] = []
    overlay.picked.connect(picked.append)
    overlay.cancelled.connect(lambda: cancelled.append(True))

    overlay._cancel()

    assert cancelled == [True]
    assert picked == []


def test_screen_color_picker_tracks_mouse_without_button_press() -> None:
    _qt_app_or_skip()
    desktop = _desktop(20, 20, 1.0, QRect(1, 1, 2, 2), "#ffffff")
    overlay = ScreenColorPickerOverlay(desktop, QRect(0, 0, 20, 20))

    assert overlay.hasMouseTracking()


def test_screen_color_picker_batches_live_capture_refresh() -> None:
    _qt_app_or_skip()
    desktop = _desktop(20, 20, 1.0, QRect(1, 1, 2, 2), "#ffffff")
    overlay = ScreenColorPickerOverlay(desktop, QRect(0, 0, 20, 20))

    overlay._queue_sample_refresh()
    overlay._queue_sample_refresh()

    assert overlay._sample_refresh_timer.isSingleShot()
    assert overlay._sample_refresh_timer.interval() == COLOR_PICKER_REFRESH_INTERVAL_MS
    assert overlay._sample_refresh_timer.isActive()


def test_screen_color_picker_current_color_uses_live_region(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _qt_app_or_skip()
    desktop = _desktop(100, 80, 1.0, QRect(42, 32, 1, 1), "#000000")
    live = _desktop(17, 17, 1.0, QRect(8, 8, 1, 1), "#ff00aa")
    overlay = ScreenColorPickerOverlay(desktop, QRect(0, 0, 100, 80))
    overlay.cursor_pos = QPoint(42, 32)
    requested: list[QRect] = []

    def fake_capture(rect: QRect):  # type: ignore[no-untyped-def]
        requested.append(QRect(rect))
        return live, QRect(34, 24, 17, 17)

    monkeypatch.setattr("app.ui.screen_color_picker.capture_live_screen_region", fake_capture)

    assert overlay._current_color() == "#ff00aa"
    assert requested == [QRect(34, 24, 17, 17)]


def test_screen_color_picker_preview_source_uses_live_region(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _qt_app_or_skip()
    desktop = _desktop(100, 80, 1.0, QRect(42, 32, 1, 1), "#000000")
    live = _desktop(17, 17, 1.0, QRect(8, 8, 1, 1), "#12abef")
    overlay = ScreenColorPickerOverlay(desktop, QRect(0, 0, 100, 80))
    overlay.cursor_pos = QPoint(42, 32)

    def fake_capture(rect: QRect):  # type: ignore[no-untyped-def]
        assert rect == QRect(34, 24, 17, 17)
        return live, QRect(34, 24, 17, 17)

    monkeypatch.setattr("app.ui.screen_color_picker.capture_live_screen_region", fake_capture)
    sample = overlay._sample_source(overlay._sample_rect())

    assert sample is not None
    pixmap, _rect, color = sample
    assert color == "#12abef"
    assert sample_pixmap_hex_color(pixmap, QPoint(8, 8)) == "#12abef"


def test_screen_color_picker_paint_uses_cached_or_fallback_sample(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _qt_app_or_skip()
    desktop = _desktop(220, 180, 1.0, QRect(100, 80, 1, 1), "#123456")
    overlay = ScreenColorPickerOverlay(desktop, QRect(0, 0, 220, 180))
    overlay.cursor_pos = QPoint(100, 80)
    target = QPixmap(220, 180)
    target.fill(QColor(0, 0, 0, 0))

    def fail_capture(_rect: QRect):  # type: ignore[no-untyped-def]
        raise AssertionError("paint must not grab the screen")

    monkeypatch.setattr("app.ui.screen_color_picker.capture_live_screen_region", fail_capture)
    painter = QPainter(target)
    try:
        overlay._draw_preview(painter)
    finally:
        painter.end()
