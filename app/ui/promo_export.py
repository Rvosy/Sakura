from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

from app.ui.control_panel_layout import PetLayout
from app.ui.input_blur_background import make_blurred_pixmap


class PromoExportError(RuntimeError):
    """Raised when a transparent promotional image cannot be rendered."""


@dataclass(frozen=True)
class PromoExportGeometry:
    scale: float
    canvas_size: tuple[int, int]
    portrait_rect: tuple[int, int, int, int]
    bubble_rect: tuple[int, int, int, int]
    input_rect: tuple[int, int, int, int]


def compute_promo_export_geometry(
    source_size: tuple[int, int],
    display_layout: PetLayout,
) -> PromoExportGeometry:
    """Scale the displayed composition so the portrait reaches its source resolution."""

    source_width, source_height = source_size
    _, _, display_width, display_height = display_layout.portrait_rect
    if source_width <= 0 or source_height <= 0:
        raise PromoExportError("立绘原图尺寸无效。")
    if display_width <= 0 or display_height <= 0:
        raise PromoExportError("当前立绘显示尺寸无效。")

    # KeepAspectRatio can round one display edge by a pixel. Averaging the two
    # ratios keeps every UI element on one uniform scale without stretching it.
    scale = (
        source_width / display_width + source_height / display_height
    ) / 2.0
    canvas_width = max(1, round(display_layout.window_size[0] * scale))
    canvas_height = max(1, round(display_layout.window_size[1] * scale))

    px, py, pw, ph = display_layout.portrait_rect
    portrait_center_x = (px + pw / 2.0) * scale
    portrait_bottom = (py + ph) * scale
    portrait_rect = (
        round(portrait_center_x - source_width / 2.0),
        round(portrait_bottom - source_height),
        source_width,
        source_height,
    )

    return PromoExportGeometry(
        scale=scale,
        canvas_size=(canvas_width, canvas_height),
        portrait_rect=portrait_rect,
        bubble_rect=_scaled_rect(display_layout.bubble_rect, scale),
        input_rect=_scaled_rect(display_layout.input_rect, scale),
    )


def render_promo_image(
    *,
    portrait_path: Path,
    display_layout: PetLayout,
    bubble: QWidget,
    input_card: QWidget,
) -> tuple[QImage, PromoExportGeometry]:
    """Render portrait and controls onto a transparent, source-resolution canvas."""

    portrait = QImage(str(portrait_path))
    if portrait.isNull():
        raise PromoExportError(f"无法读取立绘原图：{portrait_path}")

    geometry = compute_promo_export_geometry(
        (portrait.width(), portrait.height()),
        display_layout,
    )
    image = QImage(
        geometry.canvas_size[0],
        geometry.canvas_size[1],
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    if image.isNull():
        raise PromoExportError(
            f"无法创建 {geometry.canvas_size[0]}×{geometry.canvas_size[1]} 的导出画布。"
        )
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    if not painter.isActive():
        raise PromoExportError("无法初始化宣传图绘制器。")
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        px, py, _, _ = geometry.portrait_rect
        # No target size is supplied: source pixels are copied without resampling.
        painter.drawImage(QPoint(px, py), portrait)
        _render_widget(painter, bubble, geometry.bubble_rect)
        _render_widget(painter, input_card, geometry.input_rect)
    finally:
        painter.end()

    return image, geometry


def build_gaussian_input_background(
    *,
    portrait_path: Path,
    display_layout: PetLayout,
    background_color: QColor | str,
) -> QPixmap:
    """Build the input glass backdrop over the supplied theme background color."""

    portrait = QImage(str(portrait_path))
    if portrait.isNull():
        raise PromoExportError(f"无法读取立绘原图：{portrait_path}")
    geometry = compute_promo_export_geometry(
        (portrait.width(), portrait.height()),
        display_layout,
    )
    input_x, input_y, input_width, input_height = geometry.input_rect
    backdrop = QPixmap(input_width, input_height)
    if backdrop.isNull():
        raise PromoExportError(
            f"无法创建 {input_width}×{input_height} 的高斯模糊背景。"
        )
    base_color = QColor(background_color)
    if not base_color.isValid():
        raise PromoExportError(f"高斯模糊底色无效：{background_color}")
    backdrop.fill(base_color)

    portrait_x, portrait_y, portrait_width, portrait_height = geometry.portrait_rect
    painter = QPainter(backdrop)
    if not painter.isActive():
        raise PromoExportError("无法初始化高斯模糊背景绘制器。")
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(
            QRect(
                portrait_x - input_x,
                portrait_y - input_y,
                portrait_width,
                portrait_height,
            ),
            portrait,
        )
    finally:
        painter.end()

    blurred = make_blurred_pixmap(
        backdrop,
        radius=max(1.0, 4.0 * geometry.scale),
        downscale=2,
    )
    blurred.setDevicePixelRatio(geometry.scale)
    return blurred


def save_promo_image(image: QImage, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output_path), "PNG"):
        raise PromoExportError(f"无法保存 PNG：{output_path}")


def _scaled_rect(
    rect: tuple[int, int, int, int],
    scale: float,
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    left = round(x * scale)
    top = round(y * scale)
    right = round((x + width) * scale)
    bottom = round((y + height) * scale)
    return left, top, max(1, right - left), max(1, bottom - top)


def _render_widget(
    painter: QPainter,
    widget: QWidget,
    target_rect: tuple[int, int, int, int],
) -> None:
    width = widget.width()
    height = widget.height()
    if width <= 0 or height <= 0:
        raise PromoExportError(f"控件 {widget.objectName() or type(widget).__name__} 尺寸无效。")

    x, y, target_width, target_height = target_rect
    painter.save()
    try:
        painter.translate(x, y)
        painter.scale(target_width / width, target_height / height)
        widget.render(
            painter,
            QPoint(0, 0),
            QRegion(QRect(0, 0, width, height)),
            QWidget.RenderFlag.DrawChildren,
        )
    finally:
        painter.restore()
