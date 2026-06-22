from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLineEdit

from app.llm.chat_reply import ChatSegment
from app.ui.theme import DEFAULT_THEME_SETTINGS, build_settings_dialog_stylesheet


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


def test_readonly_link_selection_stays_visible() -> None:
    stylesheet = build_settings_dialog_stylesheet(DEFAULT_THEME_SETTINGS)
    start = stylesheet.index('QLineEdit[readOnly="true"] {')
    end = stylesheet.index("QComboBox {", start)
    block = stylesheet[start:end]

    assert "selection-background-color: transparent" not in block
    assert "selection-color:" in block


def test_mobile_chat_completion_syncs_current_desktop_context() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _handle_mobile_chat_completed = PetWindow._handle_mobile_chat_completed
        _remember_reply_history_segments = PetWindow._remember_reply_history_segments
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index
        _can_review_reply_history = PetWindow._can_review_reply_history
        _update_reply_history_buttons = PetWindow._update_reply_history_buttons

        def _record_completed_memory_turn(self) -> None:
            self.memory_turns += 1

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
    old_segment = ChatSegment("旧回复。")
    window.reply_history_segments = [old_segment]
    window.reply_history_index = 0
    window.reply_history_review_active = False
    window.worker_thread = None
    window.subtitle_controller = SimpleNamespace(is_reply_sequence_active=lambda: False)
    window.reply_history_previous_button = Button()
    window.reply_history_next_button = Button()
    window.history_window = HistoryWindow()
    window.memory_turns = 0

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
    assert window.reply_history_segments == [old_segment, segment]
    assert window.reply_history_index == 0
    assert not window.reply_history_previous_button.enabled
    assert window.reply_history_next_button.enabled
    assert window.history_window.refreshes == 1
    assert window.memory_turns == 1


def test_mobile_chat_finish_refreshes_reply_history_buttons() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _finish_mobile_chat_worker = PetWindow._finish_mobile_chat_worker
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index
        _can_review_reply_history = PetWindow._can_review_reply_history
        _update_reply_history_buttons = PetWindow._update_reply_history_buttons

        def _start_next_mobile_chat(self) -> None:
            self.start_next_called = True

    class Button:
        def __init__(self) -> None:
            self.enabled = False

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    window = MinimalWindow()
    window.start_next_called = False
    window._active_mobile_chat_request = {"done": True}
    window.worker_thread = None
    window.reply_history_segments = [ChatSegment("旧回复。"), ChatSegment("手机回复。")]
    window.reply_history_index = 0
    window.subtitle_controller = SimpleNamespace(is_reply_sequence_active=lambda: False)
    window.reply_history_previous_button = Button()
    window.reply_history_next_button = Button()

    window._finish_mobile_chat_worker()

    assert window._active_mobile_chat_request is None
    assert window.start_next_called
    assert not window.reply_history_previous_button.enabled
    assert window.reply_history_next_button.enabled


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


def test_mobile_chat_ignores_background_memory_curation() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _mobile_chat_busy = PetWindow._mobile_chat_busy

    window = MinimalWindow()
    window.worker_thread = None
    window._active_mobile_chat_request = None
    window._mobile_chat_requests = []
    window.memory_curation_thread = object()
    window.active_reminder_id = None
    window.active_event_type = ""
    window.pending_tool_action = None
    window.pending_screen_observation_messages = None
    window.screen_observation_followup_in_progress = False
    window.screen_observation_encode_thread = None
    window.active_interaction_id = ""
    window.subtitle_controller = SimpleNamespace(is_reply_sequence_active=lambda: False)

    assert not window._mobile_chat_busy()


def test_mobile_chat_allows_stale_interaction_id_after_reply_sequence_done() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _mobile_chat_busy = PetWindow._mobile_chat_busy

    window = MinimalWindow()
    window.worker_thread = None
    window._active_mobile_chat_request = None
    window._mobile_chat_requests = []
    window.memory_curation_thread = None
    window.active_reminder_id = None
    window.active_event_type = ""
    window.pending_tool_action = None
    window.pending_screen_observation_messages = None
    window.screen_observation_followup_in_progress = False
    window.screen_observation_encode_thread = None
    window.active_interaction_id = "interaction-stale"
    window.subtitle_controller = SimpleNamespace(is_reply_sequence_active=lambda: False)

    assert not window._mobile_chat_busy()


def test_mobile_chat_is_busy_while_reply_sequence_active() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _mobile_chat_busy = PetWindow._mobile_chat_busy

    window = MinimalWindow()
    window.worker_thread = None
    window._active_mobile_chat_request = None
    window._mobile_chat_requests = []
    window.memory_curation_thread = None
    window.active_reminder_id = None
    window.active_event_type = ""
    window.pending_tool_action = None
    window.pending_screen_observation_messages = None
    window.screen_observation_followup_in_progress = False
    window.screen_observation_encode_thread = None
    window.active_interaction_id = ""
    window.subtitle_controller = SimpleNamespace(is_reply_sequence_active=lambda: True)

    assert window._mobile_chat_busy()
