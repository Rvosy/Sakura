from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

qtcore = pytest.importorskip("PySide6.QtCore")
qtgui = pytest.importorskip("PySide6.QtGui")
qtwidgets = pytest.importorskip("PySide6.QtWidgets")

if not all(
    hasattr(module, name)
    for module, name in (
        (qtcore, "Qt"),
        (qtgui, "QImage"),
        (qtwidgets, "QApplication"),
        (qtwidgets, "QWidget"),
    )
):
    pytest.skip("当前测试环境只提供了 PySide6 stub。", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from app.ui.card_container import CardContainer
from app.ui.control_panel_layout import compute_pet_layout
from app.ui.input_blur_background import InputBlurBackground
from app.ui.promo_export import (
    build_gaussian_input_background,
    compute_promo_export_geometry,
    render_promo_image,
    save_promo_image,
)


def test_geometry_scales_controls_to_source_portrait_resolution() -> None:
    layout = compute_pet_layout(
        portrait_width=200,
        portrait_height=300,
        control_panel_width=420,
        bubble_height=100,
    )

    geometry = compute_promo_export_geometry((800, 1200), layout)

    assert geometry.scale == 4.0
    assert geometry.portrait_rect[2:] == (800, 1200)
    assert geometry.bubble_rect[2] == layout.bubble_rect[2] * 4
    assert geometry.bubble_rect[3] == layout.bubble_rect[3] * 4
    assert geometry.input_rect[2] == layout.input_rect[2] * 4
    assert geometry.input_rect[3] == layout.input_rect[3] * 4


def test_rendered_promo_keeps_alpha_and_source_portrait_pixels(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    portrait_path = tmp_path / "portrait.png"
    portrait = QImage(800, 1200, QImage.Format.Format_ARGB32)
    portrait.fill(QColor(220, 30, 60, 255))
    assert portrait.save(str(portrait_path), "PNG")

    layout = compute_pet_layout(
        portrait_width=200,
        portrait_height=300,
        control_panel_width=420,
        bubble_height=100,
    )
    bubble = QWidget()
    bubble.resize(layout.bubble_rect[2], layout.bubble_rect[3])
    bubble.setStyleSheet(
        "background: rgba(30, 80, 200, 220); border-radius: 20px;"
    )
    input_card = QWidget()
    input_card.resize(layout.input_rect[2], layout.input_rect[3])
    input_card.setStyleSheet(
        "background: rgba(40, 200, 90, 220); border-radius: 22px;"
    )

    image, geometry = render_promo_image(
        portrait_path=portrait_path,
        display_layout=layout,
        bubble=bubble,
        input_card=input_card,
    )
    output_path = tmp_path / "promo.png"
    save_promo_image(image, output_path)
    reloaded = QImage(str(output_path))

    assert reloaded.hasAlphaChannel()
    assert reloaded.size() == image.size()
    assert reloaded.pixelColor(0, 0).alpha() == 0
    px, py, _, _ = geometry.portrait_rect
    assert reloaded.pixelColor(px + 10, py + 10) == QColor(220, 30, 60, 255)
    bx, by, _, _ = geometry.bubble_rect
    ix, iy, _, _ = geometry.input_rect
    assert reloaded.pixelColor(bx, by).alpha() == 0
    assert reloaded.pixelColor(ix, iy).alpha() == 0

    bubble.deleteLater()
    input_card.deleteLater()
    app.processEvents()


def test_pet_window_export_saves_png_and_restores_hidden_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    portrait_path = tmp_path / "portrait.png"
    portrait = QImage(400, 600, QImage.Format.Format_ARGB32)
    portrait.fill(QColor(200, 40, 80, 255))
    assert portrait.save(str(portrait_path), "PNG")

    layout = compute_pet_layout(
        portrait_width=200,
        portrait_height=300,
        control_panel_width=420,
        bubble_height=100,
    )
    host = QWidget()
    host.base_dir = tmp_path
    host.character_profile = SimpleNamespace(id="demo")
    host.theme_settings = SimpleNamespace(border_color="#a7e8c8")
    host.portrait_controller = SimpleNamespace(current_path=portrait_path)
    host.bubble = QWidget(host)
    host.bubble.resize(layout.bubble_rect[2], layout.bubble_rect[3])
    host.bubble_opacity_effect = QGraphicsOpacityEffect(host.bubble)
    host.bubble.setGraphicsEffect(host.bubble_opacity_effect)
    input_content = QWidget()
    host.input_blur_background = InputBlurBackground()
    host.input_card = CardContainer(
        input_content,
        background_layer=host.input_blur_background,
        parent=host,
    )
    host.input_card.resize(layout.input_rect[2], layout.input_rect[3])
    host._compute_pet_layout = lambda: layout
    host._input_bar_visual_effect_mode = lambda: "gaussian_blur"
    applied_modes: list[str] = []
    host._apply_input_bar_visual_effect_property = applied_modes.append
    host.bubble.hide()
    host.input_card.hide()

    output_without_suffix = tmp_path / "exported-promo"
    messages: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pet_window_module.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(output_without_suffix), "PNG 图片 (*.png)"),
    )
    monkeypatch.setattr(
        pet_window_module.QMessageBox,
        "information",
        lambda _parent, title, text: messages.append((title, text)),
    )
    monkeypatch.setattr(
        pet_window_module.QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )

    PetWindow.export_promo_image(host)  # type: ignore[arg-type]

    output_path = output_without_suffix.with_suffix(".png")
    assert output_path.is_file()
    exported = QImage(str(output_path))
    assert exported.hasAlphaChannel()
    assert host.bubble.isHidden()
    assert host.input_card.isHidden()
    assert host.bubble_opacity_effect.isEnabled()
    assert host.input_card.fade_effect.isEnabled()
    assert applied_modes == [pet_window_module.VisualEffectMode.GAUSSIAN_BLUR, "gaussian_blur"]
    geometry = compute_promo_export_geometry((400, 600), layout)
    ix, iy, _, _ = geometry.input_rect
    assert exported.pixelColor(ix, iy).alpha() == 0
    assert warnings == []
    assert messages == [("导出成功", f"透明宣传图已保存到：{output_path}")]

    host.deleteLater()
    app.processEvents()


def test_gaussian_background_uses_portrait_over_theme_border_color(tmp_path: Path) -> None:
    _app = QApplication.instance() or QApplication([])
    portrait_path = tmp_path / "portrait.png"
    portrait = QImage(400, 600, QImage.Format.Format_ARGB32)
    portrait.fill(QColor(220, 30, 60, 255))
    assert portrait.save(str(portrait_path), "PNG")
    layout = compute_pet_layout(
        portrait_width=200,
        portrait_height=300,
        control_panel_width=420,
        bubble_height=100,
    )

    border_color = QColor("#a7e8c8")
    backdrop = build_gaussian_input_background(
        portrait_path=portrait_path,
        display_layout=layout,
        background_color=border_color,
    )
    image = backdrop.toImage()

    assert backdrop.devicePixelRatio() == 2.0
    assert image.width() == layout.input_rect[2] * 2
    assert image.height() == layout.input_rect[3] * 2
    edge_color = image.pixelColor(10, image.height() // 2)
    assert abs(edge_color.red() - border_color.red()) < 5
    assert abs(edge_color.green() - border_color.green()) < 5
    assert abs(edge_color.blue() - border_color.blue()) < 5
    assert image.pixelColor(image.width() // 2, image.height() // 2).red() > 180
    assert image.pixelColor(image.width() // 2, image.height() // 2).green() < 100
