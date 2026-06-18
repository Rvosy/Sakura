"""app/ui/settings/widgets.py — 设置窗口的通用小控件。

从 settings_dialog.py 拆出：禁用滚轮误触的输入控件族、
点击展开的模型下拉框、仅点击选择的列表。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, QStringListModel, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QListWidget,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QSplitterHandle,
    QWidget,
)


class _NoWheelMixin:
    """禁止未获焦时响应滚轮，防止滚动设置页时意外改值。"""

    def wheelEvent(self, event):  # type: ignore[no-untyped-def]
        if self.hasFocus():  # type: ignore[attr-defined]
            super().wheelEvent(event)  # type: ignore[misc]
        else:
            event.ignore()


class _NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    pass


class _NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    pass


class _NoWheelComboBox(QComboBox):
    """仅弹出列表打开时响应滚轮，避免未展开时滚动意外切换选项。"""

    def wheelEvent(self, event):  # type: ignore[no-untyped-def]
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class _NoWheelSlider(_NoWheelMixin, QSlider):
    pass


class _FitContentScrollArea(QScrollArea):
    """纵向贴合内部控件高度的滚动区。

    QScrollArea 默认的 sizeHint 是与内容无关的经验值，放进布局里既会撑出空白、又无法
    随内容收缩。这里把 sizeHint 改成内部控件的高度：配合 `Maximum` 纵向尺寸策略，空间
    充足时正好贴合内容（不留空白），空间不足时收缩并启用内部滚动条，而不是把表单各行压到
    重叠。横向仍沿用 QScrollArea 默认值。
    """

    def sizeHint(self) -> QSize:
        widget = self.widget()
        if widget is not None:
            frame = 2 * self.frameWidth()
            hint = widget.sizeHint()
            return QSize(hint.width() + frame, hint.height() + frame)
        return super().sizeHint()


class _GripSplitterHandle(QSplitterHandle):
    """竖直 splitter 手柄:正中画一条固定短宽的圆角小条作抓取指示,其余透明。

    用纯绘制而非 QSS,手柄抓取条宽度恒定(不随窗格变宽而变长),配色由所属
    _GripSplitter 的 grip 颜色注入(随主题更新)。
    """

    _GRIP_WIDTH = 44
    _GRIP_THICKNESS = 4

    def __init__(self, orientation, parent) -> None:  # type: ignore[no-untyped-def]
        super().__init__(orientation, parent)
        self._hover = False

    def enterEvent(self, event):  # type: ignore[no-untyped-def]
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # type: ignore[no-untyped-def]
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # type: ignore[no-untyped-def]
        splitter = self.splitter()
        base = getattr(splitter, "_grip_color", None) or QColor(0, 0, 0, 70)
        hover = getattr(splitter, "_grip_hover_color", None) or QColor(0, 0, 0, 120)
        rect = self.rect()
        width = min(self._GRIP_WIDTH, rect.width())
        x = (rect.width() - width) / 2.0
        y = (rect.height() - self._GRIP_THICKNESS) / 2.0
        radius = self._GRIP_THICKNESS / 2.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(hover if self._hover else base)
        painter.drawRoundedRect(QRectF(x, y, width, self._GRIP_THICKNESS), radius, radius)


class _GripSplitter(QSplitter):
    """带固定短抓取条的分隔器;抓取条配色由 set_grip_colors 注入主题色。"""

    def __init__(self, orientation, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(orientation, parent)
        self._grip_color = QColor(0, 0, 0, 70)
        self._grip_hover_color = QColor(0, 0, 0, 120)

    def set_grip_colors(self, base: QColor, hover: QColor) -> None:
        self._grip_color = base
        self._grip_hover_color = hover
        for index in range(1, self.count()):
            self.handle(index).update()

    def createHandle(self) -> QSplitterHandle:
        return _GripSplitterHandle(self.orientation(), self)


class _ClickOnlyListWidget(QListWidget):
    """左侧分类导航列表：仅响应左键单击切换页面。

    禁用按住左键拖动时随鼠标连续切换当前项（默认 QListWidget 行为会误切页），
    同时屏蔽右键（不选中、不弹上下文菜单），避免误触。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def mousePressEvent(self, event):  # type: ignore[no-untyped-def]
        # 仅左键触发选中/切换，右键与中键直接忽略
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[no-untyped-def]
        # 按住左键拖动时不连续切换；无按键的悬停仍走默认逻辑以保留 hover 高亮
        if event.buttons() & Qt.MouseButton.LeftButton:
            event.ignore()
            return
        super().mouseMoveEvent(event)


class ModelComboBox(_NoWheelComboBox):
    """可编辑模型选择框，保留 QLineEdit 风格的 text/setText 兼容接口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model_names: list[str] = []
        self._completion_model = QStringListModel(self)
        completer = QCompleter(self._completion_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(completer)

    def setText(self, text: str) -> None:
        self.setEditText(text)

    def text(self) -> str:
        return self.currentText()

    def set_model_names(self, model_names: list[str]) -> None:
        current_text = self.currentText().strip()
        self._model_names = list(model_names)
        self.blockSignals(True)
        self.clear()
        self.addItems(self._model_names)
        self._completion_model.setStringList(self._model_names)
        if current_text:
            self.setEditText(current_text)
        elif self._model_names:
            self.setCurrentIndex(0)
        self.blockSignals(False)
