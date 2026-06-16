"""tests/ui/test_ui_state.py — UI 统一状态源测试。

覆盖：
- 状态转移与信号发射
- 重复设置同状态不重复发信号
- 语义化入口（thinking/streaming/speaking/finish/fail）
- ERROR 保持语义：失败后 finish 不触发（由 pet_window 控制），
  下一次 thinking 离开 ERROR
"""

from __future__ import annotations

import pytest

qtcore = pytest.importorskip("PySide6.QtCore")

from app.ui.state import PetUiState, PetUiStateStore


@pytest.fixture()
def store() -> PetUiStateStore:
    return PetUiStateStore()


class TestPetUiStateStore:
    def test_initial_state_is_idle(self, store: PetUiStateStore) -> None:
        assert store.state == PetUiState.IDLE

    def test_full_interaction_cycle(self, store: PetUiStateStore) -> None:
        seen: list[PetUiState] = []
        store.state_changed.connect(seen.append)
        store.begin_thinking("send_button")
        store.begin_streaming("partial_reply")
        store.begin_speaking("reply_segment")
        store.finish("reply_completed")
        assert seen == [
            PetUiState.THINKING,
            PetUiState.STREAMING,
            PetUiState.SPEAKING,
            PetUiState.IDLE,
        ]
        assert store.state == PetUiState.IDLE

    def test_same_state_not_reemitted(self, store: PetUiStateStore) -> None:
        seen: list[PetUiState] = []
        store.state_changed.connect(seen.append)
        store.begin_streaming()
        store.begin_streaming()
        store.begin_streaming()
        assert seen == [PetUiState.STREAMING]

    def test_error_then_next_interaction_recovers(self, store: PetUiStateStore) -> None:
        store.begin_thinking()
        store.fail("worker_error")
        assert store.state == PetUiState.ERROR
        # 下一次交互从 ERROR 直接进入 THINKING
        store.begin_thinking("retry")
        assert store.state == PetUiState.THINKING

    def test_set_state_with_reason_logged(self, store: PetUiStateStore) -> None:
        # 仅验证 reason 路径不抛异常（日志内容由 debug_log 测试覆盖）
        store.set_state(PetUiState.SPEAKING, reason="manual")
        assert store.state == PetUiState.SPEAKING
