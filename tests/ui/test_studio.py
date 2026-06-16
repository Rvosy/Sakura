from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.ui.theme import ThemeSettings
from tools.studio.character_doc import DEFAULT_TONE_REFS, CharacterDoc, VoiceDraft


def _qt_app():
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication") or not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _disable_audio_player(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.studio.panels import voice_panel

    monkeypatch.setattr(voice_panel, "_build_player", lambda: (None, None))


def _studio_runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "temp" / "test_runtime" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_studio_uses_eight_step_wizard_without_toolbar_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qt_app()
    _disable_audio_player(monkeypatch)

    from tools.studio.app_studio import STUDIO_STEPS, StudioWindow

    window = StudioWindow(project_root=_studio_runtime_root("studio_steps"))

    assert [label for _key, label in STUDIO_STEPS] == [
        "新建或导入角色",
        "基础信息",
        "人格卡",
        "立绘绑定",
        "语音模型",
        "添加参考音频",
        "主题配色",
        "导出",
    ]
    assert "回复语气" not in [button.text() for button in window._step_buttons]
    assert window._stack.count() == 8
    assert window._stack.currentIndex() == 0
    assert not hasattr(window, "new_button")
    assert not hasattr(window, "export_button")


def test_studio_stepper_and_footer_switch_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qt_app()
    _disable_audio_player(monkeypatch)

    from tools.studio.app_studio import StudioWindow

    window = StudioWindow(project_root=_studio_runtime_root("studio_navigation"))

    window.next_step_button.click()
    assert window._current_step == 1
    assert window._stack.currentIndex() == 1

    window._step_buttons[5].click()
    assert window._current_step == 5
    assert window._stack.currentIndex() == 5
    assert window._step_buttons[5].property("stepState") == "current"
    assert window._step_buttons[4].property("stepState") == "done"

    window.prev_step_button.click()
    assert window._current_step == 4
    assert window._stack.currentIndex() == 4


def test_reference_audio_writes_ref_file_and_reply_tones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qt_app()
    _disable_audio_player(monkeypatch)

    from tools.studio.panels.voice_panel import ReferenceAudioPanel

    package_dir = _studio_runtime_root("studio_reference_audio") / "character"
    ref_dir = package_dir / "voice" / "refs" / "tone_refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "happy.wav").write_bytes(b"wav")
    (ref_dir / "shy.wav").write_bytes(b"wav")

    panel = ReferenceAudioPanel()
    panel.bind_package_dir(package_dir)
    panel._add_ref_row("voice/refs/tone_refs/happy.wav", "JA", "うれしい", "开心")
    panel._add_ref_row("voice/refs/tone_refs/shy.wav", "JA", "照れる", "害羞")
    panel._add_ref_row("voice/refs/tone_refs/happy.wav", "JA", "もう一度", "开心")

    doc = CharacterDoc(
        id="test",
        display_name="测试",
        voice=VoiceDraft(gpt_model="voice/models/a.ckpt", sovits_model="voice/models/b.pth"),
    )
    panel.write_to(doc)

    assert doc.reply_tones == ["开心", "害羞"]
    assert doc.voice is not None
    assert doc.voice.gpt_model == "voice/models/a.ckpt"
    assert doc.voice.sovits_model == "voice/models/b.pth"
    assert doc.voice.tone_refs == DEFAULT_TONE_REFS
    assert (package_dir / DEFAULT_TONE_REFS).read_text(encoding="utf-8") == (
        "voice/refs/tone_refs/happy.wav|JA|うれしい|开心\n"
        "voice/refs/tone_refs/shy.wav|JA|照れる|害羞\n"
        "voice/refs/tone_refs/happy.wav|JA|もう一度|开心\n"
    )


def test_voice_model_and_reference_audio_merge_one_voice_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qt_app()
    _disable_audio_player(monkeypatch)

    from tools.studio.panels.voice_panel import ReferenceAudioPanel, VoiceModelPanel

    package_dir = _studio_runtime_root("studio_voice_merge") / "character"
    ref_dir = package_dir / "voice" / "refs" / "tone_refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "neutral.wav").write_bytes(b"wav")

    model_panel = VoiceModelPanel()
    model_panel.bind_package_dir(package_dir)
    model_panel.enable_check.setChecked(True)
    model_panel.gpt_edit.setText("voice/models/model.ckpt")
    model_panel.sovits_edit.setText("voice/models/model.pth")
    model_panel.ref_lang_edit.setText("ja")
    model_panel.text_lang_edit.setText("zh")

    ref_panel = ReferenceAudioPanel()
    ref_panel.bind_package_dir(package_dir)
    ref_panel._add_ref_row("voice/refs/tone_refs/neutral.wav", "JA", "テスト", "中性")

    doc = CharacterDoc(id="test", display_name="测试")
    model_panel.write_to(doc)
    ref_panel.write_to(doc)

    assert doc.voice == VoiceDraft(
        tone_refs=DEFAULT_TONE_REFS,
        gpt_model="voice/models/model.ckpt",
        sovits_model="voice/models/model.pth",
        ref_lang="ja",
        text_lang="zh",
    )
    assert doc.reply_tones == ["中性"]
    assert ref_panel.validate(doc) == []
    assert ref_panel.ref_table.verticalHeader().defaultSectionSize() >= 38


def test_portrait_panel_edits_portrait_description_tags() -> None:
    _qt_app()

    from tools.studio.panels.portrait_panel import PortraitPanel

    package_dir = _studio_runtime_root("studio_portrait_tags") / "character"
    portrait_dir = package_dir / "portraits"
    portrait_dir.mkdir(parents=True)
    (portrait_dir / "A010.png").write_bytes(b"png")
    (portrait_dir / "A020.png").write_bytes(b"png")

    panel = PortraitPanel()
    panel.bind_package_dir(package_dir)
    panel.load_from(
        CharacterDoc(
            id="test",
            display_name="测试",
            default_portrait="portraits/A010.png",
            expressions={"站立待机": "portraits/A010.png"},
        )
    )

    assert panel.portrait_table.horizontalHeaderItem(0).text() == "立绘（相对路径）"
    assert panel.portrait_table.horizontalHeaderItem(1).text() == "描述标签"
    assert "语气" not in panel.portrait_table.horizontalHeaderItem(1).text()
    assert panel.portrait_table.verticalHeader().defaultSectionSize() >= 38

    rows = [panel._rows()[row] for row in range(panel.portrait_table.rowCount())]
    assert ("portraits/A010.png", "站立待机") in rows
    assert ("portraits/A020.png", "") in rows

    for row, (rel, _label) in enumerate(panel._rows()):
        if rel == "portraits/A020.png":
            panel.portrait_table.item(row, 1).setText("开心")
            break

    doc = CharacterDoc(id="test", display_name="测试")
    panel.write_to(doc)

    assert doc.expressions == {
        "站立待机": "portraits/A010.png",
        "开心": "portraits/A020.png",
    }
    assert doc.default_portrait == "portraits/A010.png"


def test_theme_swatches_keep_individual_colors() -> None:
    _qt_app()

    from tools.studio.panels.theme_panel import ThemePanel

    panel = ThemePanel()
    doc = CharacterDoc(
        theme=ThemeSettings(
            primary_color="#111111",
            primary_hover_color="#222222",
            accent_color="#333333",
            text_color="#444444",
            secondary_text_color="#555555",
            muted_text_color="#666666",
            page_background_color="#777777",
            panel_background_color="#888888",
            input_background_color="#999999",
            bubble_background_color="#aaaaaa",
            border_color="#bbbbbb",
        )
    )

    panel.load_from(doc)
    styles = {field: button.styleSheet() for field, button in panel._swatches.items()}

    assert "#111111" in styles["primary_color"]
    assert "#333333" in styles["accent_color"]
    assert "#bbbbbb" in styles["border_color"]
    assert len(set(styles.values())) > 1


def test_theme_panel_changes_apply_to_studio_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qt_app()
    _disable_audio_player(monkeypatch)

    from tools.studio.app_studio import StudioWindow

    window = StudioWindow(project_root=_studio_runtime_root("studio_theme_live"))
    theme_panel = window._panels["theme"]

    theme_panel._edits["primary_color"].setText("#123456")

    assert "#123456" in window.styleSheet()


def test_studio_styles_table_corner_and_theme_editor_backgrounds() -> None:
    _qt_app()

    from PySide6.QtWidgets import QWidget

    from tools.studio.panels.theme_panel import ThemePanel
    from tools.studio.styles import build_studio_stylesheet

    theme = ThemeSettings(
        panel_background_color="#112233",
        border_color="#445566",
        input_background_color="#778899",
    )
    stylesheet = build_studio_stylesheet(theme)
    panel = ThemePanel()

    assert "QTableCornerButton::section" in stylesheet
    assert "rgba(17, 34, 51" in stylesheet
    assert panel.findChild(QWidget, "themeEditorGrid") is not None
    assert panel.findChild(QWidget, "themeEditorViewport") is not None


def test_studio_theme_updates_palette_and_base_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qt_app()
    _disable_audio_player(monkeypatch)

    from PySide6.QtGui import QPalette

    from tools.studio.app_studio import StudioWindow

    window = StudioWindow(project_root=_studio_runtime_root("studio_palette"))
    theme = ThemeSettings(
        primary_color="#123456",
        accent_color="#234567",
        text_color="#345678",
        muted_text_color="#456789",
        page_background_color="#56789a",
        panel_background_color="#6789ab",
        input_background_color="#789abc",
    )

    window._apply_theme(theme)

    assert app.palette().color(QPalette.ColorRole.Window).name() == "#56789a"
    assert app.palette().color(QPalette.ColorRole.Base).name() == "#789abc"
    assert window.font().pixelSize() >= 14 or window.font().pointSize() >= 11
