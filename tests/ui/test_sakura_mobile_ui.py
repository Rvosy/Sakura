from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLineEdit

from app.llm.chat_reply import ChatSegment


def test_mobile_settings_panel_shows_token_and_links() -> None:
    from plugins.sakura_mobile.settings_panel import SakuraMobileSettingsPanel

    app = QApplication.instance() or QApplication([])
    _ = app

    class Plugin:
        def config(self) -> dict[str, object]:
            return {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 8765,
                "token": "secret",
            }

        def status(self) -> dict[str, object]:
            return {
                **self.config(),
                "running": True,
                "error": "",
                "local_url": "http://127.0.0.1:8765/?token=secret",
                "lan_urls": ["http://192.168.1.23:8765/?token=secret"],
            }

    panel = SakuraMobileSettingsPanel(Plugin())

    assert panel.token.echoMode() == QLineEdit.EchoMode.Normal
    assert panel.token.text() == "secret"
    assert panel.status_label.text() == "运行中"
    assert panel.local_url.text() == "http://127.0.0.1:8765/?token=secret"
    assert panel.lan_url.text() == "http://192.168.1.23:8765/?token=secret"

    panel._copy(panel.local_url.text(), panel.copy_local_button)

    assert QApplication.clipboard().text() == "http://127.0.0.1:8765/?token=secret"
    assert panel.copy_local_button.text() == "已复制"


def test_mobile_chat_completion_syncs_current_desktop_context() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _handle_mobile_chat_completed = PetWindow._handle_mobile_chat_completed
        _remember_reply_history_segments = PetWindow._remember_reply_history_segments
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index
        _can_review_reply_history = PetWindow._can_review_reply_history
        _update_reply_history_buttons = PetWindow._update_reply_history_buttons

    class Button:
        def __init__(self) -> None:
            self.enabled = False

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class HistoryWindow:
        def __init__(self) -> None:
            self.refreshes = 0

        def request_refresh(self) -> None:
            self.refreshes += 1

    window = MinimalWindow()
    window.character_profile = SimpleNamespace(id="demo")
    window.messages = [{"role": "user", "content": "桌面旧消息"}]
    window.reply_history_segments = []
    window.reply_history_index = None
    window.reply_history_review_active = False
    window.worker_thread = None
    window.subtitle_controller = SimpleNamespace(is_reply_sequence_active=lambda: False)
    window.reply_history_previous_button = Button()
    window.reply_history_next_button = Button()
    window.history_window = HistoryWindow()

    segment = ChatSegment("返事。", "中性", "回复。", "站立待机")
    window._handle_mobile_chat_completed(
        {
            "character_id": "demo",
            "user_text": "手机消息",
            "assistant_text": "返事。",
            "segments": [segment],
        }
    )

    assert window.messages == [
        {"role": "user", "content": "桌面旧消息"},
        {"role": "user", "content": "手机消息"},
        {"role": "assistant", "content": "返事。"},
    ]
    assert window.reply_history_segments == [segment]
    assert window.reply_history_index == 0
    assert window.history_window.refreshes == 1


def test_mobile_chat_completion_ignores_other_character() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _handle_mobile_chat_completed = PetWindow._handle_mobile_chat_completed

    window = MinimalWindow()
    window.character_profile = SimpleNamespace(id="current")
    window.messages = []
    window._handle_mobile_chat_completed(
        {
            "character_id": "other",
            "user_text": "手机消息",
            "assistant_text": "返事。",
            "segments": [ChatSegment("返事。")],
        }
    )

    assert window.messages == []
