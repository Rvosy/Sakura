from __future__ import annotations


def test_pet_window_initializes_the_primary_user_surface(pet_window) -> None:  # type: ignore[no-untyped-def]
    assert pet_window.character_profile.id == "demo"
    assert pet_window.name_label.text() == "Demo"
    assert pet_window.input_edit.isEnabled()
    assert pet_window.send_button.isEnabled()
    assert pet_window.send_button.text() == "发送"
    assert not pet_window.startup_initializing


def test_pet_window_startup_mode_locks_mutating_controls(startup_pet_window) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import STARTUP_INITIALIZING_TEXT

    assert startup_pet_window.startup_initializing
    assert startup_pet_window.speech_label.text() == STARTUP_INITIALIZING_TEXT
    assert not startup_pet_window.input_edit.isEnabled()
    assert not startup_pet_window.send_button.isEnabled()
    assert not startup_pet_window.screenshot_button.isEnabled()


def test_pet_window_menu_keeps_only_product_actions(pet_window) -> None:  # type: ignore[no-untyped-def]
    menu = pet_window._build_menu()
    actions = [action for action in menu.actions() if not action.isSeparator()]
    texts = {action.text() for action in actions}
    checkable = {action.text() for action in actions if action.isCheckable()}

    assert {"隐藏至托盘", "历史记录", "运行日志", "设置", "退出"} <= texts
    assert checkable == {"显示中文字幕", "完整访问权限", "保持置顶"}
    menu.deleteLater()


def test_pet_window_busy_and_tray_states_round_trip(pet_window, qtbot) -> None:  # type: ignore[no-untyped-def]
    pet_window._set_busy(True)
    assert pet_window.input_edit.isEnabled()
    assert not pet_window.send_button.isEnabled()
    assert not pet_window.screenshot_button.isEnabled()
    assert pet_window.send_button.text() == "等待"

    pet_window._set_busy(False)
    assert pet_window.send_button.isEnabled()
    assert pet_window.screenshot_button.isEnabled()
    assert pet_window.send_button.text() == "发送"

    pet_window._hide_to_tray()
    qtbot.wait(1)
    assert pet_window.hidden_to_tray
    assert not pet_window.isVisible()

    pet_window._show_from_tray()
    qtbot.wait(1)
    assert not pet_window.hidden_to_tray
    assert pet_window.isVisible()
