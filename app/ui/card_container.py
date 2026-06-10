from __future__ import annotations

from PySide6.QtWidgets import QGraphicsOpacityEffect, QVBoxLayout, QWidget


class CardContainer(QWidget):
    """承载气泡 / 输入栏内容的「窗口内」卡片容器（替代独立顶层 AcrylicCardWindow）。

    作为 PetWindow 的子控件随主窗口单帧合成，不再是独立 HWND，因此彻底消除了三窗口
    跨进程合成的同步竞态（立绘上跳 / 气泡下沉侵入输入栏 / 调参抖动）。

    - 自带一个用于 hover / 自动隐藏淡入淡出的 `fade_effect`（QGraphicsOpacityEffect）；
      其 opacity 属性可被 QPropertyAnimation 驱动，替代原先子窗口的 windowOpacity。
      与内容自身的脉冲 effect（气泡浮现 / 输入栏发送反馈）互不干扰：父子两层
      QGraphicsOpacityEffect 在 Qt 中按乘积合成。
    - 可选 `background_layer` 铺在最底层并随容器尺寸变化（输入栏软件高斯模糊背景）。
    - 卡片自身透明，圆角与底色由内容控件的 QSS 负责（由主窗口样式表级联）。
    """

    def __init__(
        self,
        content: QWidget,
        *,
        background_layer: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # 软件模糊背景层：不进 layout，手动铺满并置底，由内容控件的透明背景透出。
        self._background_layer = background_layer
        if background_layer is not None:
            background_layer.setParent(self)
            background_layer.lower()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content)  # 将既有控件 reparent 进卡片容器
        self._content = content

        # hover / 自动隐藏淡入淡出的目标 effect（与内容脉冲 effect 分离）。
        self.fade_effect = QGraphicsOpacityEffect(self)
        self.fade_effect.setOpacity(1.0)
        self.setGraphicsEffect(self.fade_effect)

    @property
    def content(self) -> QWidget:
        return self._content

    def set_background_layer(self, background_layer: QWidget | None) -> None:
        """切换软件模糊背景层（外观模式变化时调用）。"""
        if self._background_layer is background_layer:
            return
        if self._background_layer is not None:
            self._background_layer.hide()
        self._background_layer = background_layer
        if background_layer is not None:
            background_layer.setParent(self)
            background_layer.setGeometry(self.rect())
            background_layer.lower()
            background_layer.show()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self._background_layer is not None:
            self._background_layer.setGeometry(self.rect())
            self._background_layer.lower()
