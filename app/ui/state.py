"""app/ui/state.py — 桌宠 UI 统一状态源。

输入栏等待动效、气泡三点动画、TTS 播放指示此前各自由独立定时器驱动，
没有可观测的全局状态。PetUiStateStore 作为单一状态源：
- pet_window 在交互生命周期节点驱动状态转移
- 动效组件通过 state_changed 信号订阅（增量迁移，见 C12）
- 每次转移写结构化日志 ui.state {from,to,reason}，配合 interaction_id
  可直接回答"用户报障时 UI 卡在哪个阶段"
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QObject, Signal

from app.core.debug_log import debug_log


class PetUiState(str, Enum):
    IDLE = "idle"          # 空闲，等待输入
    THINKING = "thinking"  # 已发送请求，等待模型首包
    STREAMING = "streaming"  # 正在接收/展示流式回复
    SPEAKING = "speaking"  # TTS 播放中
    ERROR = "error"        # 最近一次交互失败（下一次交互离开）


class PetUiStateStore(QObject):
    """UI 状态的唯一持有者；只能通过 set_state 转移。"""

    state_changed = Signal(object)  # PetUiState

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = PetUiState.IDLE

    @property
    def state(self) -> PetUiState:
        return self._state

    def set_state(self, new_state: PetUiState, reason: str = "") -> None:
        if new_state == self._state:
            return
        old_state = self._state
        self._state = new_state
        debug_log(
            "UI",
            "ui.state",
            {"from": old_state.value, "to": new_state.value, "reason": reason},
        )
        self.state_changed.emit(new_state)

    # ---- 语义化转移入口：调用点表达意图而不是状态值 ----
    def begin_thinking(self, reason: str = "") -> None:
        self.set_state(PetUiState.THINKING, reason or "interaction_started")

    def begin_streaming(self, reason: str = "") -> None:
        self.set_state(PetUiState.STREAMING, reason or "progress_received")

    def begin_speaking(self, reason: str = "") -> None:
        self.set_state(PetUiState.SPEAKING, reason or "tts_started")

    def finish(self, reason: str = "") -> None:
        self.set_state(PetUiState.IDLE, reason or "interaction_finished")

    def fail(self, reason: str = "") -> None:
        self.set_state(PetUiState.ERROR, reason or "interaction_failed")
