"""StudioWindow —— SakuraCharacterStudio 主窗口。

布局：顶部标题 + 左侧流程线 + 右侧向导编辑区。
复用主项目 app.ui.theme 的样式生成函数，使编辑器与桌宠保持同一套视觉。

角色包生命周期：新建/导入 → 各面板编辑 → 保存（草稿写盘）/ 导出（校验后打包 .char）。
所有编辑都落在工作区 tools/studio/workspace/，不触碰主项目 characters/ 生产目录。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.character_loader import CharacterConfigError
from app.ui.fonts import _rounded_chinese_font
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    ThemeSettings,
    build_app_chrome_stylesheet,
    build_settings_dialog_stylesheet,
)

from tools.studio.character_doc import CharacterDoc
from tools.studio.panels.base import StudioPanel
from tools.studio.panels.basic_panel import ID_PATTERN, BasicInfoPanel, PersonaPanel
from tools.studio.panels.flow_panel import ExportPanel, StartPanel
from tools.studio.panels.portrait_panel import PortraitPanel
from tools.studio.panels.theme_panel import ThemePanel
from tools.studio.panels.voice_panel import ReferenceAudioPanel, VoiceModelPanel
from tools.studio.styles import build_studio_stylesheet
from tools.studio.workspace import Workspace

# 向导步骤：(key, 显示名)
STUDIO_STEPS: list[tuple[str, str]] = [
    ("start", "新建或导入角色"),
    ("basic", "基础信息"),
    ("persona", "人格卡"),
    ("portrait", "立绘绑定"),
    ("voice_model", "语音模型"),
    ("reference_audio", "添加参考音频"),
    ("theme", "主题配色"),
    ("export", "导出"),
]


class StudioWindow(QMainWindow):
    """角色包编辑器主窗口。"""

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = Path(project_root)
        self.workspace = Workspace(self.project_root / "tools" / "studio" / "workspace")
        self._theme: ThemeSettings = DEFAULT_THEME_SETTINGS.normalized()
        self._panels: dict[str, StudioPanel] = {}
        self._doc: CharacterDoc | None = None
        self._package_dir: Path | None = None
        self._current_step = 0
        self._step_buttons: list[QPushButton] = []
        self._step_lines: list[QFrame] = []

        self.setWindowTitle("SakuraCharacterStudio · 角色包工坊")
        self.setMinimumSize(980, 660)
        self.resize(1120, 760)
        self.setFont(_rounded_chinese_font(11, QFont.Weight.Normal))

        self._build_ui()
        self._apply_theme(self._theme)

    # ---- UI 构建 ----------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("studioCentral")
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        root.addLayout(self._build_toolbar())
        root.addLayout(self._build_body(), stretch=1)

        self._status_label = QLabel("未打开角色包 —— 新建或打开一个角色包开始编辑")
        self._status_label.setObjectName("studioStatus")
        root.addWidget(self._status_label)

        self.setCentralWidget(central)

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        title = QLabel("SakuraCharacterStudio")
        title.setObjectName("studioTitle")
        title.setFont(_rounded_chinese_font(15, QFont.Weight.DemiBold))
        bar.addWidget(title)
        bar.addStretch(1)

        return bar

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(12)

        self._stack = QStackedWidget()
        self._stack.setObjectName("studioStack")

        for key, label in STUDIO_STEPS:
            panel = self._create_panel(key, label)
            self._panels[key] = panel
            self._stack.addWidget(panel)

        content = QWidget()
        content.setObjectName("studioContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(self._stack, 1)
        content_layout.addWidget(self._build_wizard_footer())

        body.addWidget(self._build_stepper())
        body.addWidget(content, stretch=1)
        self._go_to_step(0)
        return body

    def _create_panel(self, key: str, label: str) -> StudioPanel:
        if key == "start":
            panel = StartPanel()
            panel.new_requested.connect(self._on_new)
            panel.open_dir_requested.connect(self._on_open_dir)
            panel.open_char_requested.connect(self._on_open_char)
            return panel
        if key == "basic":
            return BasicInfoPanel()
        if key == "persona":
            return PersonaPanel()
        if key == "portrait":
            return PortraitPanel()
        if key == "voice_model":
            return VoiceModelPanel()
        if key == "reference_audio":
            return ReferenceAudioPanel()
        if key == "theme":
            panel = ThemePanel()
            panel.theme_changed.connect(self._on_theme_changed)
            return panel
        if key == "export":
            panel = ExportPanel()
            panel.save_requested.connect(self._on_save)
            panel.export_requested.connect(self._on_export)
            return panel
        raise ValueError(f"未知 Studio 步骤：{label}")

    def _build_stepper(self) -> QWidget:
        stepper = QWidget()
        stepper.setObjectName("studioStepper")
        stepper.setFixedWidth(220)
        layout = QVBoxLayout(stepper)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(0)

        heading = QLabel("角色包流程")
        heading.setObjectName("studioStepperTitle")
        layout.addWidget(heading)

        for index, (_key, label) in enumerate(STUDIO_STEPS):
            button = QPushButton(f"{index + 1:02d}  {label}")
            button.setObjectName("studioStepButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self._go_to_step(i))
            self._step_buttons.append(button)
            layout.addWidget(button)

            if index < len(STUDIO_STEPS) - 1:
                line = QFrame()
                line.setObjectName("studioStepLine")
                line.setFrameShape(QFrame.Shape.VLine)
                self._step_lines.append(line)
                layout.addWidget(line)

        layout.addStretch(1)
        return stepper

    def _build_wizard_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("studioWizardFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        self._step_indicator_label = QLabel()
        self._step_indicator_label.setObjectName("studioStepIndicator")
        layout.addWidget(self._step_indicator_label)
        layout.addStretch(1)

        self.prev_step_button = QPushButton("上一步")
        self.prev_step_button.setObjectName("studioSecondaryButton")
        self.prev_step_button.clicked.connect(lambda: self._go_to_step(self._current_step - 1))
        self.next_step_button = QPushButton("下一步")
        self.next_step_button.setObjectName("studioPrimaryButton")
        self.next_step_button.clicked.connect(lambda: self._go_to_step(self._current_step + 1))
        layout.addWidget(self.prev_step_button)
        layout.addWidget(self.next_step_button)
        return footer

    def _go_to_step(self, index: int) -> None:
        if not (0 <= index < self._stack.count()):
            return
        self._current_step = index
        self._stack.setCurrentIndex(index)
        self._sync_stepper()
        if STUDIO_STEPS[index][0] == "export":
            self._refresh_export_panel()

    def _sync_stepper(self) -> None:
        for index, button in enumerate(self._step_buttons):
            state = "current" if index == self._current_step else "done" if index < self._current_step else "todo"
            button.setChecked(index == self._current_step)
            button.setProperty("stepState", state)
            self._refresh_widget_style(button)

        for index, line in enumerate(self._step_lines):
            line.setProperty("lineState", "done" if index < self._current_step else "todo")
            self._refresh_widget_style(line)

        _key, label = STUDIO_STEPS[self._current_step]
        self._step_indicator_label.setText(f"第 {self._current_step + 1} / {len(STUDIO_STEPS)} 步 · {label}")
        self.prev_step_button.setEnabled(self._current_step > 0)
        self.next_step_button.setEnabled(self._current_step < len(STUDIO_STEPS) - 1)

    def _refresh_widget_style(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _refresh_export_panel(self) -> None:
        export_panel = self._panels.get("export")
        if not isinstance(export_panel, ExportPanel):
            return
        if self._doc is None or self._package_dir is None:
            export_panel.set_ready(False)
            return
        doc = self._write_panels_to_doc(write_files=False)
        if doc is not None:
            export_panel.bind_package_dir(self._package_dir)
            export_panel.load_from(doc)

    # ---- 主题 -------------------------------------------------------------

    def _apply_theme(self, theme: ThemeSettings) -> None:
        """套用主项目主题样式，使 Studio 与桌宠观感一致。"""
        theme = theme.normalized()
        self._theme = theme
        self._apply_theme_palette(theme)
        self.setStyleSheet(
            build_app_chrome_stylesheet(theme) + build_settings_dialog_stylesheet(theme)
            + build_studio_stylesheet(theme)
        )

    def _on_theme_changed(self, theme: ThemeSettings) -> None:
        self._apply_theme(theme)

    def _apply_theme_palette(self, theme: ThemeSettings) -> None:
        """同步 Qt palette，避免未完全 QSS 化的原生子控件残留旧配色。"""
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(theme.page_background_color))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text_color))
        palette.setColor(QPalette.ColorRole.Base, QColor(theme.input_background_color))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.panel_background_color))
        palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_color))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_color))
        palette.setColor(QPalette.ColorRole.Button, QColor(theme.panel_background_color))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme.input_background_color))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme.text_color))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme.muted_text_color))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.primary_color))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Link, QColor(theme.accent_color))

        app = QApplication.instance()
        if app is not None:
            app.setPalette(palette)
        self.setPalette(palette)

    # ---- 角色包生命周期 ---------------------------------------------------

    def _set_doc(self, doc: CharacterDoc, package_dir: Path) -> None:
        self._doc = doc
        self._package_dir = package_dir
        for panel in self._panels.values():
            panel.bind_package_dir(package_dir)
            panel.load_from(doc)
        export_panel = self._panels.get("export")
        if isinstance(export_panel, ExportPanel):
            export_panel.set_ready(True)
        self._go_to_step(1)
        self._status_label.setText(f"已打开：{package_dir}")

    def _write_panels_to_doc(self, *, write_files: bool = True) -> CharacterDoc | None:
        if self._doc is None:
            return None
        for key, _label in STUDIO_STEPS:
            panel = self._panels[key]
            if isinstance(panel, ReferenceAudioPanel):
                panel.write_to(self._doc, write_files=write_files)
            else:
                panel.write_to(self._doc)
        return self._doc

    def _collect_validation_errors(self, doc: CharacterDoc) -> list[str]:
        errors: list[str] = []
        for key, _label in STUDIO_STEPS:
            errors.extend(self._panels[key].validate(doc))
        return errors

    def _confirm_overwrite_workspace(self, char_id: str) -> bool:
        if not self.workspace.package_dir(char_id).exists():
            return True
        reply = QMessageBox.question(
            self,
            "覆盖草稿",
            f"工作区已存在角色「{char_id}」的草稿，继续会删除旧草稿。是否覆盖？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # ---- 按钮处理 ---------------------------------------------------------

    def _on_new(self) -> None:
        char_id, ok = QInputDialog.getText(
            self, "新建角色包", "角色 ID（仅字母、数字、_ . -）："
        )
        if not ok:
            return
        char_id = char_id.strip()
        if not char_id or not ID_PATTERN.match(char_id):
            QMessageBox.warning(self, "无效的角色 ID", "角色 ID 只能包含字母、数字、_ . -")
            return
        if not self._confirm_overwrite_workspace(char_id):
            return
        pkg, doc = self.workspace.new_character(char_id)
        self._set_doc(doc, pkg)
        self._status_label.setText(f"已新建：{char_id}（请补充立绘并指定默认立绘后再导出）")

    def _on_open_dir(self) -> None:
        start = str(self.project_root / "characters")
        directory = QFileDialog.getExistingDirectory(self, "选择角色包目录", start)
        if not directory:
            return
        src_dir = Path(directory)
        if not self._confirm_overwrite_workspace(src_dir.name):
            return
        try:
            pkg, doc = self.workspace.open_directory(src_dir)
        except Exception as exc:  # noqa: BLE001 - 统一弹窗反馈
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self._set_doc(doc, pkg)

    def _on_open_char(self) -> None:
        start = str(self.project_root)
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 .char 角色包", start, "Sakura 角色包 (*.char)"
        )
        if not path:
            return
        try:
            pkg, doc = self.workspace.open_archive(Path(path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._set_doc(doc, pkg)

    def _on_save(self) -> None:
        doc = self._write_panels_to_doc()
        if doc is None or self._package_dir is None:
            return
        try:
            self.workspace.save(doc, self._package_dir)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._status_label.setText(f"已保存到工作区：{self._package_dir}")

    def _on_export(self) -> None:
        doc = self._write_panels_to_doc()
        if doc is None or self._package_dir is None:
            return

        errors = self._collect_validation_errors(doc)
        if errors:
            QMessageBox.warning(self, "无法导出", "请先修正以下问题：\n\n" + "\n".join(f"· {e}" for e in errors))
            return

        suggested = str((self.project_root / f"{doc.id or 'character'}.char"))
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 .char", suggested, "Sakura 角色包 (*.char)"
        )
        if not path:
            return
        try:
            self.setEnabled(False)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            try:
                self.workspace.export(doc, self._package_dir, Path(path))
            finally:
                QApplication.restoreOverrideCursor()
                self.setEnabled(True)
        except CharacterConfigError as exc:
            QMessageBox.warning(self, "校验未通过", f"角色包存在问题，无法导出：\n\n{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._status_label.setText(f"已导出：{path}")
        QMessageBox.information(self, "导出成功", f"已导出角色包：\n{path}")
