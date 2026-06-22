"""语音模型与参考音频编辑面板。"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
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

from tools.studio.character_doc import DEFAULT_TONE_REFS, CharacterDoc, VoiceDraft
from tools.studio.panels.base import StudioPanel

GPT_MODEL_EXT = ".ckpt"
SOVITS_MODEL_EXT = ".pth"
GPT_MODEL_FILTER = "GPT 模型 (*.ckpt);;所有文件 (*)"
SOVITS_MODEL_FILTER = "SoVITS 模型 (*.pth);;所有文件 (*)"
AUDIO_FILTER = "音频 (*.ogg *.wav *.mp3 *.flac);;所有文件 (*)"
MODELS_SUBDIR = "voice/models"
TONE_REFS_SUBDIR = "voice/refs/tone_refs"
REF_COLUMNS = ["音频（相对路径）", "语言", "参考文本", "语气标签"]


def _build_player():
    """尝试创建 QtMultimedia 播放器；不可用时返回 (None, None)。"""
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except Exception:  # noqa: BLE001 - 缺少多媒体后端时优雅降级
        return None, None
    player = QMediaPlayer()
    audio = QAudioOutput()
    player.setAudioOutput(audio)
    return player, audio


def _copy_into(package_dir: Path, src: Path, subdir: str) -> str:
    """把文件复制到工作区 subdir 下，返回相对包目录的 POSIX 路径。"""
    target_dir = package_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target_dir / src.name)
    return f"{subdir}/{src.name}"



def _remove_previous_model(package_dir: Path, old_rel: str, keep_rel: str) -> None:
    if not old_rel or old_rel == keep_rel:
        return
    models_dir = (package_dir / MODELS_SUBDIR).resolve()
    old_path = (package_dir / old_rel).resolve()
    try:
        old_path.relative_to(models_dir)
    except ValueError:
        return
    if old_path.is_file():
        old_path.unlink(missing_ok=True)
def _with_button(edit: QLineEdit, text: str, slot) -> QWidget:
    wrap = QWidget()
    wrap.setObjectName("studioInlineField")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)
    btn = QPushButton(text)
    btn.clicked.connect(slot)
    row.addWidget(btn)
    return wrap


class VoiceModelPanel(StudioPanel):
    """语音模型步骤：启用状态、模型文件和默认语言。"""

    def __init__(self) -> None:
        super().__init__()
        self._package_dir: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        title = QLabel("语音模型")
        title.setObjectName("studioPanelTitle")
        root.addWidget(title)

        self.enable_check = QCheckBox("启用语音（GPT-SoVITS）")
        self.enable_check.toggled.connect(self._sync_enabled)
        root.addWidget(self.enable_check)

        self.body = QWidget()
        self.body.setObjectName("voiceModelBody")
        form = QFormLayout(self.body)
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        self.gpt_edit = QLineEdit()
        self.gpt_edit.setReadOnly(True)
        self.gpt_edit.setPlaceholderText("可选，选择 .ckpt 模型")
        self.sovits_edit = QLineEdit()
        self.sovits_edit.setReadOnly(True)
        self.sovits_edit.setPlaceholderText("可选，选择 .pth 模型")
        self.ref_lang_edit = QLineEdit("ja")
        self.text_lang_edit = QLineEdit("ja")

        form.addRow("GPT 模型", _with_button(self.gpt_edit, "选择…", self._pick_gpt))
        form.addRow("SoVITS 模型", _with_button(self.sovits_edit, "选择…", self._pick_sovits))
        form.addRow("默认参考语言", self.ref_lang_edit)
        form.addRow("合成目标语言", self.text_lang_edit)

        root.addWidget(self.body)
        root.addStretch(1)
        self._sync_enabled(False)

    def bind_package_dir(self, package_dir: Path) -> None:
        self._package_dir = Path(package_dir)

    def _sync_enabled(self, enabled: bool) -> None:
        self.body.setEnabled(enabled)

    def _pick_gpt(self) -> None:
        self._pick_model(self.gpt_edit, GPT_MODEL_EXT, GPT_MODEL_FILTER, "GPT 模型")

    def _pick_sovits(self) -> None:
        self._pick_model(self.sovits_edit, SOVITS_MODEL_EXT, SOVITS_MODEL_FILTER, "SoVITS 模型")

    def _pick_model(self, edit: QLineEdit, expected_ext: str, file_filter: str, label: str) -> None:
        if self._package_dir is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择模型文件", "", file_filter)
        if path:
            if Path(path).suffix.lower() != expected_ext:
                QMessageBox.warning(self, "模型类型不匹配", f"{label} 请选择 {expected_ext} 文件。")
                return
            old_rel = edit.text().strip()
            self.setEnabled(False)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            try:
                new_rel = _copy_into(self._package_dir, Path(path), MODELS_SUBDIR)
                _remove_previous_model(self._package_dir, old_rel, new_rel)
            finally:
                QApplication.restoreOverrideCursor()
                self.setEnabled(True)
            edit.setText(new_rel)

    def load_from(self, doc: CharacterDoc) -> None:
        voice = doc.voice
        self.enable_check.setChecked(voice is not None)
        self._sync_enabled(voice is not None)
        self.gpt_edit.setText(voice.gpt_model or "" if voice else "")
        self.sovits_edit.setText(voice.sovits_model or "" if voice else "")
        self.ref_lang_edit.setText(voice.ref_lang if voice else "ja")
        self.text_lang_edit.setText(voice.text_lang if voice else "ja")

    def validate(self, doc: CharacterDoc) -> list[str]:
        if not self.enable_check.isChecked():
            return []
        errors: list[str] = []
        for label, edit, expected_ext in (
            ("GPT 模型", self.gpt_edit, GPT_MODEL_EXT),
            ("SoVITS 模型", self.sovits_edit, SOVITS_MODEL_EXT),
        ):
            path_text = edit.text().strip()
            if path_text and Path(path_text).suffix.lower() != expected_ext:
                errors.append(f"{label} 必须是 {expected_ext} 文件")
        return errors

    def write_to(self, doc: CharacterDoc) -> None:
        if not self.enable_check.isChecked():
            doc.voice = None
            return

        current = doc.voice or VoiceDraft()
        doc.voice = VoiceDraft(
            tone_refs=current.tone_refs or DEFAULT_TONE_REFS,
            gpt_model=self.gpt_edit.text().strip() or None,
            sovits_model=self.sovits_edit.text().strip() or None,
            ref_lang=self.ref_lang_edit.text().strip() or "ja",
            text_lang=self.text_lang_edit.text().strip() or "ja",
        )


class ReferenceAudioPanel(StudioPanel):
    """参考音频步骤：维护 ref.txt，并从语气标签生成 reply.tones。"""

    def __init__(self) -> None:
        super().__init__()
        self._package_dir: Path | None = None
        self._player, self._audio = _build_player()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        title = QLabel("添加参考音频")
        title.setObjectName("studioPanelTitle")
        root.addWidget(title)

        subtitle = QLabel("语气标签会同步写入 reply.tones，并作为 TTS 选择参考音频的键。")
        subtitle.setObjectName("studioSectionLabel")
        root.addWidget(subtitle)

        self.ref_table = QTableWidget(0, len(REF_COLUMNS))
        self.ref_table.setHorizontalHeaderLabels(REF_COLUMNS)
        header = self.ref_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.ref_table.verticalHeader().setDefaultSectionSize(40)
        self.ref_table.verticalHeader().setMinimumSectionSize(38)
        self.ref_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.ref_table, 1)

        btns = QHBoxLayout()
        import_audio_btn = QPushButton("导入参考音频")
        import_audio_btn.clicked.connect(self._import_audio)
        add_row_btn = QPushButton("添加空行")
        add_row_btn.clicked.connect(lambda: self._add_ref_row("", "ja", "", ""))
        del_row_btn = QPushButton("删除行")
        del_row_btn.clicked.connect(self._remove_ref_row)
        self.play_btn = QPushButton("试听")
        self.play_btn.clicked.connect(self._play_selected)
        self.play_btn.setEnabled(self._player is not None)
        if self._player is None:
            self.play_btn.setToolTip("当前环境缺少音频后端，试听不可用")
        for button in (import_audio_btn, add_row_btn, del_row_btn, self.play_btn):
            btns.addWidget(button)
        btns.addStretch(1)
        root.addLayout(btns)

    def bind_package_dir(self, package_dir: Path) -> None:
        self._package_dir = Path(package_dir)

    def _import_audio(self) -> None:
        if self._package_dir is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择参考音频", "", AUDIO_FILTER)
        for path in paths:
            rel = _copy_into(self._package_dir, Path(path), TONE_REFS_SUBDIR)
            self._add_ref_row(rel, "ja", "", "")

    def _add_ref_row(self, audio: str, lang: str, text: str, tone: str) -> None:
        row = self.ref_table.rowCount()
        self.ref_table.insertRow(row)
        self.ref_table.setRowHeight(row, 40)
        for col, value in enumerate((audio, lang, text, tone)):
            self.ref_table.setItem(row, col, QTableWidgetItem(value))

    def _remove_ref_row(self) -> None:
        row = self.ref_table.currentRow()
        if row >= 0:
            self.ref_table.removeRow(row)

    def _play_selected(self) -> None:
        if self._player is None or self._package_dir is None:
            return
        row = self.ref_table.currentRow()
        if row < 0:
            return
        rel = self._cell(row, 0)
        abs_path = self._package_dir / rel if rel else None
        if abs_path is None or not abs_path.exists():
            QMessageBox.warning(self, "试听", f"音频文件不存在：{rel}")
            return
        self._player.setSource(QUrl.fromLocalFile(str(abs_path)))
        self._player.play()

    def load_from(self, doc: CharacterDoc) -> None:
        voice = doc.voice
        self._load_ref_table(voice.tone_refs if voice else DEFAULT_TONE_REFS)

    def _load_ref_table(self, ref_rel: str) -> None:
        self.ref_table.setRowCount(0)
        if self._package_dir is None:
            return
        ref_path = self._package_dir / ref_rel
        if not ref_path.exists():
            return
        for raw in ref_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            parts = raw.split("|", 3)
            while len(parts) < len(REF_COLUMNS):
                parts.append("")
            self._add_ref_row(parts[0], parts[1], parts[2], parts[3])

    def write_to(self, doc: CharacterDoc, *, write_files: bool = True) -> None:
        rows = self._rows()
        tones = self._tone_labels(rows)
        if tones:
            doc.reply_tones = tones

        if write_files and self._package_dir is not None and (doc.voice is not None or rows):
            ref_path = self._package_dir / DEFAULT_TONE_REFS
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["|".join(row) for row in rows if any(row)]
            ref_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        if doc.voice is not None:
            doc.voice = VoiceDraft(
                tone_refs=DEFAULT_TONE_REFS,
                gpt_model=doc.voice.gpt_model,
                sovits_model=doc.voice.sovits_model,
                ref_lang=doc.voice.ref_lang,
                text_lang=doc.voice.text_lang,
            )

    def validate(self, doc: CharacterDoc) -> list[str]:
        if doc.voice is None:
            return []

        errors: list[str] = []
        valid_rows = []
        for row_index, row in enumerate(self._rows(), start=1):
            if not any(row):
                continue
            if not all(row):
                errors.append(f"参考音频第 {row_index} 行未填写完整")
                continue
            audio_rel, _lang, _text, _tone = row
            if self._package_dir is not None and not (self._package_dir / audio_rel).exists():
                errors.append(f"参考音频文件不存在：{audio_rel}")
                continue
            valid_rows.append(row)

        if not valid_rows:
            errors.append("启用语音后至少需要一条完整参考音频")
        return errors

    def _cell(self, row: int, col: int) -> str:
        item = self.ref_table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _rows(self) -> list[list[str]]:
        return [
            [self._cell(row, col) for col in range(len(REF_COLUMNS))]
            for row in range(self.ref_table.rowCount())
        ]

    def _tone_labels(self, rows: list[list[str]]) -> list[str]:
        tones: list[str] = []
        seen: set[str] = set()
        for row in rows:
            tone = row[3].strip() if len(row) > 3 else ""
            if tone and tone not in seen:
                tones.append(tone)
                seen.add(tone)
        return tones


class VoicePanel(StudioPanel):
    """兼容旧入口的语音组合面板。"""

    def __init__(self) -> None:
        super().__init__()
        self.model_panel = VoiceModelPanel()
        self.reference_panel = ReferenceAudioPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.model_panel)
        layout.addWidget(self.reference_panel, 1)

    def bind_package_dir(self, package_dir: Path) -> None:
        self.model_panel.bind_package_dir(package_dir)
        self.reference_panel.bind_package_dir(package_dir)

    def load_from(self, doc: CharacterDoc) -> None:
        self.model_panel.load_from(doc)
        self.reference_panel.load_from(doc)

    def write_to(self, doc: CharacterDoc) -> None:
        self.model_panel.write_to(doc)
        self.reference_panel.write_to(doc)

    def validate(self, doc: CharacterDoc) -> list[str]:
        return self.reference_panel.validate(doc)
