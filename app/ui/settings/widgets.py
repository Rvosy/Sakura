"""app/ui/settings/widgets.py — 设置窗口的通用小控件。

从 settings_dialog.py 拆出：禁用滚轮误触的输入控件族、
点击展开的模型下拉框、仅点击选择的列表。
"""

from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QListWidget,
    QSlider,
    QSpinBox,
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
