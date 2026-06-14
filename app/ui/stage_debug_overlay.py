from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class StageDebugOverlay(QWidget):
    """舞台调试可视化层(env SAKURA_STAGE_DEBUG=1 启用)。

    画出三个矩形 + 关键数值,用于看清舞台碰撞区、并诊断 mac HiDPI 下逻辑/物理坐标错配:
      - 红框 = 窗口/舞台 rect(整窗,也就是当前的碰撞/可拖动区);
      - 绿框 = 布局 compute_pet_layout 算出的 portrait_rect(它"以为"立绘在哪);
      - 蓝框 = 实际立绘 QLabel 的 geometry()(立绘控件真正在哪)。
    三框 + 可见立绘若对不齐,即暴露逻辑/物理像素或锚点数学的问题。
    纯展示层:鼠标穿透、无系统背景,绝不影响交互。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._portrait_rect: QRect | None = None
        self._label_rect: QRect | None = None
        self._info: str = ""

    def update_debug(
        self,
        *,
        portrait_rect: QRect | None,
        label_rect: QRect | None,
        info: str,
    ) -> None:
        self._portrait_rect = portrait_rect
        self._label_rect = label_rect
        self._info = info
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._stroke(painter, self.rect(), QColor(255, 60, 60), "stage / window (碰撞区)")
        if self._portrait_rect is not None:
            self._stroke(painter, self._portrait_rect, QColor(60, 220, 90), "layout portrait_rect")
        if self._label_rect is not None:
            self._stroke(painter, self._label_rect, QColor(80, 150, 255), "label.geometry()")
        if self._info:
            self._draw_info(painter)
        painter.end()

    def _stroke(self, painter: QPainter, rect: QRect, color: QColor, label: str) -> None:
        pen = QPen(color)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.drawText(rect.adjusted(5, 3, -5, -5), int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft), label)

    def _draw_info(self, painter: QPainter) -> None:
        font = QFont()
        font.setPointSize(10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        # 半透明黑底提升可读性。
        lines = self._info.count("\n") + 1
        box = QRect(8, 8, max(260, self.width() - 16), 18 * lines + 10)
        painter.fillRect(box, QColor(0, 0, 0, 150))
        painter.setPen(QColor(255, 235, 60))
        painter.drawText(box.adjusted(6, 4, -6, -4), int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft), self._info)
