"""立绘导入与描述标签编辑面板。

功能：
- 一键导入整个文件夹的立绘到工作区 portraits/；
- 填入「立绘说明文件」批量把文件前缀匹配到描述标签（每行：前缀 标签）；
- 像参考音频页一样用表格维护「立绘相对路径 / 描述标签」；
- 指定默认立绘，并在选中表格行时显示预览。

说明：底层仍写入 character.json 的 portrait.expressions，结构是
「描述标签 -> 立绘相对路径」。UI 上不再呈现为“语气 -> 立绘”，避免和
reply.tones / TTS 语气混淆。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tools.studio.character_doc import CharacterDoc
from tools.studio.panels.base import StudioPanel

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
PORTRAITS_SUBDIR = "portraits"
PORTRAIT_COLUMNS = ["立绘（相对路径）", "描述标签"]
_PREVIEW = 330
_ROW_HEIGHT = 40


class PortraitPanel(StudioPanel):
    def __init__(self) -> None:
        super().__init__()
        self._package_dir: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        title = QLabel("立绘绑定")
        title.setObjectName("studioPanelTitle")
        root.addWidget(title)

        subtitle = QLabel("给每张立绘填写描述标签；导出时写入 portrait.expressions，和 TTS 语气标签互不混用。")
        subtitle.setObjectName("studioSectionLabel")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("立绘说明文件"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("如 .../portraits/立绘说明.txt（每行：前缀 标签）")
        desc_row.addWidget(self.desc_edit, 1)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_desc)
        bind_btn = QPushButton("按说明批量填标签")
        bind_btn.clicked.connect(self._batch_bind)
        desc_row.addWidget(browse_btn)
        desc_row.addWidget(bind_btn)
        root.addLayout(desc_row)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addLayout(self._build_table_area(), 1)
        body.addLayout(self._build_preview_area())
        root.addLayout(body, 1)

    # ---- UI ---------------------------------------------------------------

    def _build_table_area(self) -> QVBoxLayout:
        col = QVBoxLayout()
        self.portrait_table = QTableWidget(0, len(PORTRAIT_COLUMNS))
        self.portrait_table.setHorizontalHeaderLabels(PORTRAIT_COLUMNS)
        header = self.portrait_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.portrait_table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        self.portrait_table.verticalHeader().setMinimumSectionSize(_ROW_HEIGHT - 2)
        self.portrait_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.portrait_table.currentCellChanged.connect(self._on_row_selected)
        self.portrait_table.itemChanged.connect(self._on_table_item_changed)
        col.addWidget(self.portrait_table, 1)

        btns = QHBoxLayout()
        import_btn = QPushButton("导入文件夹立绘")
        import_btn.clicked.connect(self._import_folder)
        sync_btn = QPushButton("扫描立绘到表格")
        sync_btn.clicked.connect(self._sync_rows_from_library)
        add_btn = QPushButton("添加空行")
        add_btn.clicked.connect(lambda: self._add_portrait_row("", ""))
        del_btn = QPushButton("删除行")
        del_btn.clicked.connect(self._remove_row)
        for button in (import_btn, sync_btn, add_btn, del_btn):
            btns.addWidget(button)
        btns.addStretch(1)
        col.addLayout(btns)

        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("默认立绘"))
        self.default_combo = QComboBox()
        self.default_combo.currentIndexChanged.connect(self._on_default_changed)
        default_row.addWidget(self.default_combo, 1)
        col.addLayout(default_row)
        return col

    def _build_preview_area(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(QLabel("预览"))
        self.preview = QLabel("（选择立绘查看预览）")
        self.preview.setObjectName("portraitPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(_PREVIEW, _PREVIEW)
        col.addWidget(self.preview, 1)
        return col

    # ---- 资源扫描 ---------------------------------------------------------

    def _portraits_dir(self) -> Path | None:
        if self._package_dir is None:
            return None
        return self._package_dir / PORTRAITS_SUBDIR

    def _portrait_files(self) -> list[str]:
        """工作区 portraits/ 下图片的相对路径（POSIX，相对包目录），按名排序。"""
        pdir = self._portraits_dir()
        if pdir is None or not pdir.exists():
            return []
        return [
            f"{PORTRAITS_SUBDIR}/{p.name}"
            for p in sorted(pdir.iterdir())
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        ]

    def _abs_path(self, rel: str) -> Path | None:
        if self._package_dir is None or not rel:
            return None
        return self._package_dir / rel

    # ---- 面板接口 ---------------------------------------------------------

    def bind_package_dir(self, package_dir: Path) -> None:
        self._package_dir = Path(package_dir)

    def load_from(self, doc: CharacterDoc) -> None:
        self.portrait_table.setRowCount(0)
        for label, rel in doc.expressions.items():
            self._add_portrait_row(rel, label)
        self._sync_rows_from_library(show_message=False)
        self._reload_default_combo(doc.default_portrait)
        self._show_preview(doc.default_portrait)

    def write_to(self, doc: CharacterDoc) -> None:
        expressions: dict[str, str] = {}
        for rel, label in self._rows():
            if rel and label:
                expressions[label] = rel
        doc.expressions = expressions
        doc.default_portrait = str(self.default_combo.currentData() or "")

    def validate(self, doc: CharacterDoc) -> list[str]:
        errors: list[str] = []
        if not doc.default_portrait:
            errors.append("未指定默认立绘")

        seen_labels: set[str] = set()
        for row_index, (rel, label) in enumerate(self._rows(), start=1):
            if not rel and not label:
                continue
            if not rel or not label:
                errors.append(f"立绘标签第 {row_index} 行未填写完整")
                continue
            if label in seen_labels:
                errors.append(f"立绘描述标签重复：{label}")
            seen_labels.add(label)
            if self._package_dir is not None and not (self._package_dir / rel).exists():
                errors.append(f"立绘文件不存在：{rel}")
        return errors

    # ---- 表格操作 ---------------------------------------------------------

    def _cell(self, row: int, col: int) -> str:
        item = self.portrait_table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _rows(self) -> list[tuple[str, str]]:
        return [
            (self._cell(row, 0), self._cell(row, 1))
            for row in range(self.portrait_table.rowCount())
        ]

    def _add_portrait_row(self, rel: str, label: str) -> None:
        row = self.portrait_table.rowCount()
        self.portrait_table.insertRow(row)
        self.portrait_table.setRowHeight(row, _ROW_HEIGHT)
        for col, value in enumerate((rel, label)):
            item = QTableWidgetItem(value)
            self.portrait_table.setItem(row, col, item)
        self._reload_default_combo(self.default_combo.currentData() or "")

    def _remove_row(self) -> None:
        row = self.portrait_table.currentRow()
        if row >= 0:
            self.portrait_table.removeRow(row)
            self._reload_default_combo(self.default_combo.currentData() or "")

    def _find_row_by_rel(self, rel: str) -> int:
        for row in range(self.portrait_table.rowCount()):
            if self._cell(row, 0) == rel:
                return row
        return -1

    def _set_label_for_rel(self, rel: str, label: str) -> None:
        row = self._find_row_by_rel(rel)
        if row < 0:
            self._add_portrait_row(rel, label)
            return
        item = self.portrait_table.item(row, 1)
        if item is None:
            self.portrait_table.setItem(row, 1, QTableWidgetItem(label))
        else:
            item.setText(label)

    def _sync_rows_from_library(self, *, show_message: bool = True) -> None:
        files = self._portrait_files()
        existing = {rel for rel, _label in self._rows() if rel}
        added = 0
        for rel in files:
            if rel not in existing:
                self._add_portrait_row(rel, "")
                added += 1
        selected = self.default_combo.currentData() or (files[0] if files else "")
        self._reload_default_combo(str(selected))
        if selected:
            self._show_preview(str(selected))
        if show_message:
            QMessageBox.information(self, "扫描立绘", f"已补充 {added} 张未列入表格的立绘。")

    def _reload_default_combo(self, selected_rel: str) -> None:
        files: list[str] = []
        seen: set[str] = set()
        for rel, _label in self._rows():
            if rel and rel not in seen:
                files.append(rel)
                seen.add(rel)
        for rel in self._portrait_files():
            if rel and rel not in seen:
                files.append(rel)
                seen.add(rel)

        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("（未选择）", "")
        for rel in files:
            self.default_combo.addItem(Path(rel).name, rel)
        idx = self.default_combo.findData(selected_rel) if selected_rel else 0
        self.default_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.default_combo.blockSignals(False)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._reload_default_combo(self.default_combo.currentData() or "")
        if item.row() == self.portrait_table.currentRow():
            self._show_preview(self._cell(item.row(), 0))

    # ---- 导入与批量绑定 ---------------------------------------------------

    def _import_folder(self) -> None:
        if self._package_dir is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择含立绘的文件夹")
        if not directory:
            return
        pdir = self._portraits_dir()
        pdir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for p in sorted(Path(directory).iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                shutil.copy2(p, pdir / p.name)
                copied += 1
        self._sync_rows_from_library(show_message=False)
        QMessageBox.information(self, "导入立绘", f"已导入 {copied} 张立绘到工作区 portraits/")

    def _browse_desc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择立绘说明文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self.desc_edit.setText(path)

    def _batch_bind(self) -> None:
        path_text = self.desc_edit.text().strip()
        if not path_text:
            QMessageBox.information(self, "批量填标签", "请先填写立绘说明文件路径")
            return
        desc_path = Path(path_text)
        if not desc_path.exists():
            QMessageBox.warning(self, "批量填标签", f"说明文件不存在：{desc_path}")
            return

        files = self._portrait_files()
        by_stem = {Path(rel).stem: rel for rel in files}
        by_name = {Path(rel).name: rel for rel in files}

        matched = 0
        unmatched: list[str] = []
        first_rel = ""
        for raw in desc_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                unmatched.append(line)
                continue
            token, label = parts[0].strip(), parts[1].strip()
            rel = by_name.get(token) or by_stem.get(Path(token).stem)
            if not rel:
                unmatched.append(line)
                continue
            self._set_label_for_rel(rel, label)
            matched += 1
            if not first_rel:
                first_rel = rel

        if first_rel and not (self.default_combo.currentData() or ""):
            self._reload_default_combo(first_rel)

        msg = f"已填入 {matched} 条描述标签。"
        if unmatched:
            preview = "\n".join(f"· {u}" for u in unmatched[:10])
            more = f"\n...等共 {len(unmatched)} 行" if len(unmatched) > 10 else ""
            msg += f"\n\n以下 {len(unmatched)} 行未匹配到立绘文件：\n{preview}{more}"
        QMessageBox.information(self, "批量填标签结果", msg)

    # ---- 预览 -------------------------------------------------------------

    def _on_row_selected(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        if row >= 0:
            self._show_preview(self._cell(row, 0))

    def _on_default_changed(self, _index: int) -> None:
        self._show_preview(self.default_combo.currentData())

    def _show_preview(self, rel) -> None:
        abs_path = self._abs_path(str(rel)) if rel else None
        if abs_path is None or not abs_path.exists():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("（无预览）")
            return
        pix = QPixmap(str(abs_path))
        if pix.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("（无法加载图片）")
            return
        self.preview.setText("")
        self.preview.setPixmap(
            pix.scaled(
                _PREVIEW,
                _PREVIEW,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
