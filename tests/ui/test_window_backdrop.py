from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor  # noqa: E402

from app.ui import window_backdrop as wb  # noqa: E402


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def test_gradient_color_packs_abgr() -> None:
    # QColor(r,g,b,a) → 亚克力 GradientColor 0xAABBGGRR。
    assert wb._gradient_color(QColor(0x12, 0x34, 0x56, 0x78)) == 0x78563412


def test_available_modes_omit_windows_acrylic_on_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wb.sys, "platform", "win32")
    monkeypatch.setattr(wb, "_windows_build", lambda: 22631)

    assert wb.VisualEffectMode.available_modes() == [
        wb.VisualEffectMode.SOLID,
        wb.VisualEffectMode.GAUSSIAN_BLUR,
    ]


def test_available_modes_keep_macos_native_blur_on_macos(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wb.sys, "platform", "darwin")

    assert wb.VisualEffectMode.available_modes() == [
        wb.VisualEffectMode.SOLID,
        wb.VisualEffectMode.GAUSSIAN_BLUR,
        wb.VisualEffectMode.MACOS_VISUAL_EFFECT,
    ]


def test_create_window_backdrop_acrylic_on_win11(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wb.sys, "platform", "win32")
    monkeypatch.setattr(wb, "_windows_build", lambda: 22631)
    backdrop = wb.create_window_backdrop()
    assert isinstance(backdrop, wb.WindowsAcrylicBackdrop)
    assert backdrop.supports_native_blur() is True


def test_create_window_backdrop_acrylic_on_win10(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wb.sys, "platform", "win32")
    monkeypatch.setattr(wb, "_windows_build", lambda: 19045)
    assert isinstance(wb.create_window_backdrop(), wb.WindowsAcrylicBackdrop)


def test_create_window_backdrop_fallback_on_old_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wb.sys, "platform", "win32")
    monkeypatch.setattr(wb, "_windows_build", lambda: 9600)  # Win8.1
    backdrop = wb.create_window_backdrop()
    assert isinstance(backdrop, wb.FallbackTintBackdrop)
    assert backdrop.supports_native_blur() is False


def test_create_window_backdrop_fallback_on_non_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wb.sys, "platform", "linux")
    assert isinstance(wb.create_window_backdrop(), wb.FallbackTintBackdrop)


def test_backdrops_apply_remove_do_not_raise() -> None:
    from PySide6.QtWidgets import QWidget

    _qt_app_or_skip()
    widget = QWidget()
    widget.resize(120, 52)
    tint = QColor(255, 255, 255, 40)

    # 任一 backdrop 的 apply/remove 都不应抛异常（失败时内部 try/except 降级）。
    for backdrop in (
        wb.FallbackTintBackdrop(),
        wb.SoftwareBlurBackdrop(),
        wb.WindowsAcrylicBackdrop(rounded=False),
        wb.WindowsAcrylicBackdrop(rounded=True),
    ):
        backdrop.apply(widget, tint)
        backdrop.remove(widget)

    widget.deleteLater()


def test_software_blur_backdrop_is_not_native() -> None:
    # 软件截图模糊属于静态自绘，不算系统级实时模糊。
    backdrop = wb.SoftwareBlurBackdrop()
    assert backdrop.supports_native_blur() is False


def test_windows_acrylic_remove_resets_native_corner_preference(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class WindowStub:
        def winId(self) -> int:  # noqa: N802 - Qt API 兼容命名。
            return 12345

    calls: list[tuple[str, int, int]] = []
    backdrop = wb.WindowsAcrylicBackdrop(rounded=True)
    monkeypatch.setattr(
        backdrop,
        "_set_accent",
        lambda hwnd, state, _tint: calls.append(("accent", hwnd, state)),
    )
    monkeypatch.setattr(
        backdrop,
        "_set_corner_preference",
        lambda hwnd, value: calls.append(("corner", hwnd, value)),
    )

    backdrop.remove(WindowStub())  # type: ignore[arg-type]

    assert calls == [
        ("accent", 12345, backdrop._ACCENT_DISABLED),  # noqa: SLF001
        ("corner", 12345, backdrop._DWMWCP_DONOTROUND),  # noqa: SLF001
    ]


def test_card_container_background_layer_fills_and_lowers() -> None:
    from PySide6.QtWidgets import QWidget

    app = _qt_app_or_skip()
    from app.ui.card_container import CardContainer

    content = QWidget()
    background = QWidget()
    card = CardContainer(content, background_layer=background)
    # 背景层被 reparent 进卡片容器，且不进 layout（由 resizeEvent 手动铺满）。
    assert background.parentWidget() is card
    # 内容控件被 reparent 进容器。
    assert content.parentWidget() is card
    card.show()
    app.processEvents()
    card.resize(320, 52)
    app.processEvents()
    assert background.geometry() == card.rect()

    card.deleteLater()


def test_card_container_set_background_layer_swaps_layer() -> None:
    from PySide6.QtWidgets import QWidget

    app = _qt_app_or_skip()
    from app.ui.card_container import CardContainer

    content = QWidget()
    card = CardContainer(content)  # 初始无背景层（纯色模式）
    card.resize(200, 52)
    card.show()
    app.processEvents()

    background = QWidget()
    card.set_background_layer(background)  # 切到软件模糊模式：挂载并铺满
    assert background.parentWidget() is card
    assert background.geometry() == card.rect()

    card.set_background_layer(None)  # 切回纯色：背景层隐藏
    assert not background.isVisible()

    card.deleteLater()
