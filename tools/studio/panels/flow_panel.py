"""Studio 流程入口与导出面板。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from tools.studio.character_doc import CharacterDoc
from tools.studio.panels.base import StudioPanel


class StartPanel(StudioPanel):
    """新建或导入角色的向导入口。"""

    new_requested = Signal()
    open_dir_requested = Signal()
    open_char_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._package_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title = QLabel("新建或导入角色")
        title.setObjectName("studioPanelTitle")
        layout.addWidget(title)

        hint = QLabel("选择一个起点：创建空白角色包、导入现有角色目录，或打开 Sakura .char 归档。")
        hint.setObjectName("studioSectionLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QFrame()
        actions.setObjectName("studioActionCard")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(14, 14, 14, 14)
        action_layout.setSpacing(10)

        self.new_button = QPushButton("新建角色")
        self.new_button.setObjectName("studioPrimaryButton")
        self.open_dir_button = QPushButton("导入角色目录")
        self.open_dir_button.setObjectName("studioSecondaryButton")
        self.open_char_button = QPushButton("导入 .char")
        self.open_char_button.setObjectName("studioSecondaryButton")
        self.new_button.clicked.connect(lambda _checked=False: self.new_requested.emit())
        self.open_dir_button.clicked.connect(lambda _checked=False: self.open_dir_requested.emit())
        self.open_char_button.clicked.connect(lambda _checked=False: self.open_char_requested.emit())
        action_layout.addWidget(self.new_button)
        action_layout.addWidget(self.open_dir_button)
        action_layout.addWidget(self.open_char_button)
        action_layout.addStretch(1)
        layout.addWidget(actions)

        self.current_label = QLabel("当前：未打开角色包")
        self.current_label.setObjectName("studioStatus")
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)
        layout.addStretch(1)

    def bind_package_dir(self, package_dir: Path) -> None:
        self._package_dir = Path(package_dir)

    def load_from(self, doc: CharacterDoc) -> None:
        name = doc.display_name or doc.id or "未命名角色"
        path_text = f"\n工作区：{self._package_dir}" if self._package_dir else ""
        self.current_label.setText(f"当前：{name}{path_text}")


class ExportPanel(StudioPanel):
    """保存草稿并导出 .char 的向导收尾步骤。"""

    save_requested = Signal()
    export_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._package_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title = QLabel("导出")
        title.setObjectName("studioPanelTitle")
        layout.addWidget(title)

        hint = QLabel("导出前会先保存草稿并执行角色包校验；校验通过后生成 Sakura .char 文件。")
        hint.setObjectName("studioSectionLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.summary_label = QLabel("尚未打开角色包，先回到第一步新建或导入。")
        self.summary_label.setObjectName("studioStatus")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        actions = QFrame()
        actions.setObjectName("studioActionCard")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(14, 14, 14, 14)
        action_layout.setSpacing(10)
        action_layout.addStretch(1)

        self.save_button = QPushButton("保存草稿")
        self.save_button.setObjectName("studioSecondaryButton")
        self.export_button = QPushButton("导出 .char")
        self.export_button.setObjectName("studioPrimaryButton")
        self.save_button.clicked.connect(lambda _checked=False: self.save_requested.emit())
        self.export_button.clicked.connect(lambda _checked=False: self.export_requested.emit())
        action_layout.addWidget(self.save_button)
        action_layout.addWidget(self.export_button)
        layout.addWidget(actions)
        layout.addStretch(1)

        self.set_ready(False)

    def bind_package_dir(self, package_dir: Path) -> None:
        self._package_dir = Path(package_dir)

    def load_from(self, doc: CharacterDoc) -> None:
        name = doc.display_name or doc.id or "未命名角色"
        portrait_text = doc.default_portrait or "未指定默认立绘"
        path_text = f"\n工作区：{self._package_dir}" if self._package_dir else ""
        self.summary_label.setText(
            f"准备导出：{name}\n角色 ID：{doc.id or '未填写'}\n默认立绘：{portrait_text}{path_text}"
        )
        self.set_ready(True)

    def set_ready(self, ready: bool) -> None:
        self.save_button.setEnabled(ready)
        self.export_button.setEnabled(ready)
