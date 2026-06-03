from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QWidget


def build_pet_tray_menu(
    parent: QWidget,
    *,
    chinese_subtitles_checked: bool,
    free_access_checked: bool,
    always_on_top_checked: bool,
    on_hide: Callable[[], None],
    on_toggle_chinese_subtitles: Callable[[bool], None],
    on_toggle_free_access: Callable[[bool], None],
    on_toggle_always_on_top: Callable[[bool], None],
    on_show_history: Callable[[], None],
    on_show_settings: Callable[[], None],
    interactions_enabled: bool = True,
) -> QMenu:
    """构建桌宠托盘和右键菜单。"""

    menu = QMenu(parent)

    hide_action = QAction("隐藏至托盘", parent)
    hide_action.triggered.connect(on_hide)
    menu.addAction(hide_action)

    menu.addSeparator()

    subtitle_action = QAction("显示中文字幕", parent)
    subtitle_action.setCheckable(True)
    subtitle_action.setChecked(chinese_subtitles_checked)
    subtitle_action.setEnabled(interactions_enabled)
    subtitle_action.triggered.connect(on_toggle_chinese_subtitles)
    menu.addAction(subtitle_action)

    free_access_action = QAction("完整访问权限", parent)
    free_access_action.setCheckable(True)
    free_access_action.setChecked(free_access_checked)
    free_access_action.setEnabled(interactions_enabled)
    free_access_action.triggered.connect(on_toggle_free_access)
    menu.addAction(free_access_action)

    always_on_top_action = QAction("保持置顶", parent)
    always_on_top_action.setCheckable(True)
    always_on_top_action.setChecked(always_on_top_checked)
    always_on_top_action.setEnabled(interactions_enabled)
    always_on_top_action.triggered.connect(on_toggle_always_on_top)
    menu.addAction(always_on_top_action)

    menu.addSeparator()

    history_action = QAction("历史记录", parent)
    history_action.setEnabled(interactions_enabled)
    history_action.triggered.connect(on_show_history)
    menu.addAction(history_action)

    settings_action = QAction("设置", parent)
    settings_action.setEnabled(interactions_enabled)
    settings_action.triggered.connect(on_show_settings)
    menu.addAction(settings_action)

    menu.addSeparator()

    quit_action = QAction("退出", parent)
    quit_action.triggered.connect(QApplication.quit)
    menu.addAction(quit_action)

    return menu
