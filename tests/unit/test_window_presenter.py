from __future__ import annotations

from unittest.mock import MagicMock
from PySide6.QtCore import Qt
from app.ui.pet_window import _present_secondary_window


def test_present_secondary_window_when_minimized() -> None:
    # Arrange
    window = MagicMock()
    window.isMinimized.return_value = True
    window.windowState.return_value = Qt.WindowState.WindowMinimized

    # Act
    _present_secondary_window(window)

    # Assert
    window.showNormal.assert_called_once()
    window.show.assert_not_called()
    window.windowState.assert_called_once()
    window.setWindowState.assert_called_once()
    expected_state = (Qt.WindowState.WindowMinimized & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
    window.setWindowState.assert_called_with(expected_state)
    window.raise_.assert_called_once()
    window.activateWindow.assert_called_once()


def test_present_secondary_window_when_not_minimized() -> None:
    # Arrange
    window = MagicMock()
    window.isMinimized.return_value = False
    window.windowState.return_value = Qt.WindowState.WindowNoState

    # Act
    _present_secondary_window(window)

    # Assert
    window.showNormal.assert_not_called()
    window.show.assert_called_once()
    window.windowState.assert_called_once()
    window.setWindowState.assert_called_once()
    expected_state = (Qt.WindowState.WindowNoState & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
    window.setWindowState.assert_called_with(expected_state)
    window.raise_.assert_called_once()
    window.activateWindow.assert_called_once()
