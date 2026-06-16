"""回复语气（reply.tones）编辑面板。

注意：这里编辑的是提供给模型的「回复语气」列表，与立绘表情映射
（portrait.expressions）相互独立，不要混为一谈。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from tools.studio.character_doc import CharacterDoc
from tools.studio.panels.base import StudioPanel


class TonePanel(StudioPanel):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(
            QLabel("回复语气（reply.tones）—— 提供给模型选择的语气标签，留空则使用默认列表")
        )

        self.tone_list = QListWidget()
        layout.addWidget(self.tone_list, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入语气标签，回车或点「添加」")
        self.input.returnPressed.connect(self._add)
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add)
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._remove)
        up_btn = QPushButton("上移")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QPushButton("下移")
        down_btn.clicked.connect(lambda: self._move(1))
        row.addWidget(self.input, 1)
        for btn in (add_btn, del_btn, up_btn, down_btn):
            row.addWidget(btn)
        layout.addLayout(row)

    def _add(self) -> None:
        text = self.input.text().strip()
        if text and not self._contains(text):
            self.tone_list.addItem(text)
        self.input.clear()

    def _contains(self, text: str) -> bool:
        return any(self.tone_list.item(i).text() == text for i in range(self.tone_list.count()))

    def _remove(self) -> None:
        for item in self.tone_list.selectedItems():
            self.tone_list.takeItem(self.tone_list.row(item))

    def _move(self, delta: int) -> None:
        row = self.tone_list.currentRow()
        if row < 0:
            return
        target = row + delta
        if not (0 <= target < self.tone_list.count()):
            return
        item = self.tone_list.takeItem(row)
        self.tone_list.insertItem(target, item)
        self.tone_list.setCurrentRow(target)

    def load_from(self, doc: CharacterDoc) -> None:
        self.tone_list.clear()
        for tone in doc.reply_tones:
            self.tone_list.addItem(tone)

    def write_to(self, doc: CharacterDoc) -> None:
        doc.reply_tones = [
            self.tone_list.item(i).text() for i in range(self.tone_list.count())
        ]
