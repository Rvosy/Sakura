"""基础信息与人格卡编辑面板。"""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from tools.studio.character_doc import CharacterDoc
from tools.studio.panels.base import StudioPanel

# 与归档 _SAFE_CHARACTER_ID_RE 保持一致
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class BasicInfoPanel(StudioPanel):
    """角色包基础信息步骤。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        title = QLabel("基础信息")
        title.setObjectName("studioPanelTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("仅字母、数字、_ . -，如 Sakura")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("显示名，如 夜乃桜")
        self.initial_edit = QLineEdit()
        self.initial_edit.setPlaceholderText("留空使用默认欢迎语")
        form.addRow("角色 ID", self.id_edit)
        form.addRow("显示名", self.name_edit)
        form.addRow("启动欢迎语", self.initial_edit)
        layout.addLayout(form)
        layout.addStretch(1)

    def load_from(self, doc: CharacterDoc) -> None:
        self.id_edit.setText(doc.id)
        self.name_edit.setText(doc.display_name)
        self.initial_edit.setText(doc.initial_message)

    def write_to(self, doc: CharacterDoc) -> None:
        doc.id = self.id_edit.text().strip()
        doc.display_name = self.name_edit.text().strip()
        doc.initial_message = self.initial_edit.text().strip()

    def validate(self, doc: CharacterDoc) -> list[str]:
        errors: list[str] = []
        if not doc.id:
            errors.append("角色 ID 不能为空")
        elif not ID_PATTERN.match(doc.id):
            errors.append("角色 ID 只能包含字母、数字、_ . -")
        if not doc.display_name:
            errors.append("显示名不能为空")
        return errors


class PersonaPanel(StudioPanel):
    """人格卡 / 系统提示词步骤。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        title = QLabel("人格卡")
        title.setObjectName("studioPanelTitle")
        layout.addWidget(title)

        hint = QLabel("系统提示词（card.md）")
        hint.setObjectName("studioSectionLabel")
        layout.addWidget(hint)

        self.card_edit = QPlainTextEdit()
        self.card_edit.setPlaceholderText("在此编写角色的人格设定，作为系统提示词……")
        layout.addWidget(self.card_edit, 1)

    def load_from(self, doc: CharacterDoc) -> None:
        self.card_edit.setPlainText(doc.card_text)

    def write_to(self, doc: CharacterDoc) -> None:
        doc.card_text = self.card_edit.toPlainText()


class BasicPanel(StudioPanel):
    """兼容旧入口的基础信息 + 人格卡组合面板。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("仅字母、数字、_ . -，如 Sakura")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("显示名，如 夜乃桜")
        self.initial_edit = QLineEdit()
        self.initial_edit.setPlaceholderText("留空使用默认欢迎语")
        form.addRow("角色 ID", self.id_edit)
        form.addRow("显示名", self.name_edit)
        form.addRow("启动欢迎语", self.initial_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel("人格卡 / 系统提示词（card.md）"))
        self.card_edit = QPlainTextEdit()
        self.card_edit.setPlaceholderText("在此编写角色的人格设定，作为系统提示词……")
        layout.addWidget(self.card_edit, 1)

    def load_from(self, doc: CharacterDoc) -> None:
        self.id_edit.setText(doc.id)
        self.name_edit.setText(doc.display_name)
        self.initial_edit.setText(doc.initial_message)
        self.card_edit.setPlainText(doc.card_text)

    def write_to(self, doc: CharacterDoc) -> None:
        doc.id = self.id_edit.text().strip()
        doc.display_name = self.name_edit.text().strip()
        doc.initial_message = self.initial_edit.text().strip()
        doc.card_text = self.card_edit.toPlainText()

    def validate(self, doc: CharacterDoc) -> list[str]:
        return BasicInfoPanel.validate(self, doc)
