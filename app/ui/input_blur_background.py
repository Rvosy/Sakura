from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsScene, QWidget


def make_blurred_pixmap(
    src: QPixmap,
    *,
    radius: float = 8.0,
    downscale: int = 4,
) -> QPixmap:
    """对截图做廉价高斯模糊：先降采样，再小半径模糊，最后放大回原尺寸。

    截图区域只是输入栏一小块，且仅在「浮现前/拖动松手后」单次刷新调用，主线程同步即可。
    降采样为主、模糊为辅：放大用 SmoothTransformation 双线性插值天然柔化，模糊补足奶油感。
    任何步骤失败都逐级降级（仅降采样近似 → 原图），保证永不抛错、永有背景。
    """
    if src is None or src.isNull():
        return src if src is not None else QPixmap()

    dpr = src.devicePixelRatio()
    # 关键：高 DPI 桌面截图的 src 带 devicePixelRatio>1。scaled() 与 QGraphicsScene 会按
    # 「逻辑尺寸」解释带 dpr 的 pixmap，而 _blur_pixmap 用的是 size()/width() 这类「物理像素」，
    # 两者混用会把模糊结果缩到 1/dpr 并锚定到左上角，表现为输入栏背景随位置漂移的错位。
    # 因此整段处理都在物理像素尺度（dpr=1）上做，最后再把 dpr 设回去，位置与尺寸即对齐。
    work = QPixmap(src)
    work.setDevicePixelRatio(1.0)  # 仅改副本（setDevicePixelRatio 会 detach），不影响调用方 src
    base_w = max(1, work.width())
    base_h = max(1, work.height())
    factor = max(1, int(downscale))
    small_w = max(1, base_w // factor)
    small_h = max(1, base_h // factor)
    try:
        small = work.scaled(
            small_w,
            small_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        small.setDevicePixelRatio(1.0)  # scaled() 可能保留 dpr，确保中间图按物理像素处理
        blurred_small = _blur_pixmap(small, radius)
        result = blurred_small.scaled(
            base_w,
            base_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        result.setDevicePixelRatio(dpr)
        return result
    except Exception:
        # 兜底：返回原始截图，至少有「未模糊背景」可用，绝不崩。
        return src


def _blur_pixmap(pixmap: QPixmap, radius: float) -> QPixmap:
    """用 QGraphicsBlurEffect 把 pixmap 渲染成模糊版本；任何失败都原样返回。"""
    if pixmap.isNull() or radius <= 0:
        return pixmap
    try:
        scene = QGraphicsScene()
        item = scene.addPixmap(pixmap)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        item.setGraphicsEffect(effect)

        result = QImage(pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        source = QRectF(0, 0, pixmap.width(), pixmap.height())
        scene.render(painter, source, source)
        painter.end()
        return QPixmap.fromImage(result)
    except Exception:
        return pixmap


class InputBlurBackground(QWidget):
    """输入栏卡片的软件模糊背景层：绘制已模糊的桌面截图并裁成大圆角。

    纯展示层，铺满输入栏卡片窗口、置于最底，鼠标事件透传给上层输入控件。
    替代原 DWM 亚克力——亚克力是窗口级合成、无视 Qt 圆角裁剪，做不出大圆角；
    这里用 QPainterPath 自绘任意圆角，先叠主题派生的暗色遮罩压低背景亮度，再叠主题 tint 保持色调与可读性。
    """

    def __init__(self, corner_radius: float = 22.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._corner_radius = float(corner_radius)
        self._pixmap = QPixmap()
        self._shadow_overlay = QColor(0, 0, 0, 22)
        self._tint = QColor(255, 255, 255, 40)
        # 纯展示层：不抢鼠标、不画系统默认背景（全部交给 paintEvent）。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def set_blurred_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap if (pixmap is not None and not pixmap.isNull()) else QPixmap()
        self.update()

    def clear_pixmap(self) -> None:
        self._pixmap = QPixmap()
        self.update()

    def set_tint(self, tint: QColor) -> None:
        self._tint = QColor(tint)
        self.update()

    def set_shadow_overlay(self, overlay: QColor) -> None:
        self._shadow_overlay = QColor(overlay)
        self.update()

    def set_corner_radius(self, radius: float) -> None:
        self._corner_radius = float(radius)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect())
        radius = self._corner_radius

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)
        if not self._pixmap.isNull():
            # pixmap 已设 devicePixelRatio，drawPixmap 按逻辑坐标自动缩放对齐。
            painter.drawPixmap(self.rect(), self._pixmap)
        # 主题派生的暗色遮罩压住高亮桌面背景，避免磨砂层在浅色窗口上发白刺眼。
        painter.fillRect(rect, self._shadow_overlay)
        # 叠主题 tint（半透明），统一色调并在任何背景下保证内容可读。
        painter.fillRect(rect, self._tint)

        # 圆角细描边：呼应 #petInput 风格，提升卡片边缘清晰度。
        painter.setClipping(False)
        pen = QPen(QColor(255, 255, 255, 90))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(inset, radius, radius)
        painter.end()
