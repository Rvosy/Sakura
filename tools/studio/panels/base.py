"""编辑面板基类。

约定每个面板实现：
- load_from(doc): 把草稿数据加载到控件；
- write_to(doc): 把控件内容写回草稿；
- validate(doc): 返回错误信息列表（空表示无误）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from tools.studio.character_doc import CharacterDoc


class StudioPanel(QWidget):
    """所有编辑面板的基类，默认实现为空操作。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("studioPanel")

    def bind_package_dir(self, package_dir: Path) -> None:
        """绑定当前角色包目录（需要访问磁盘资源的面板覆盖此方法）。"""

    def load_from(self, doc: CharacterDoc) -> None:  # noqa: D401 - 子类覆盖
        """从草稿加载到控件。"""

    def write_to(self, doc: CharacterDoc) -> None:
        """把控件内容写回草稿。"""

    def validate(self, doc: CharacterDoc) -> list[str]:
        """返回校验错误信息（空列表表示通过）。"""
        return []


class PlaceholderPanel(StudioPanel):
    """尚未实现的面板占位，仅显示提示文字。"""

    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        hint = QLabel(f"「{title}」编辑面板（开发中）")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
