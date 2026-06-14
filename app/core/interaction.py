"""app/core/interaction.py — 交互级追踪 ID。

每次用户交互（发送消息、点击触发、主动屏幕感知事件）由 UI 层分配一个
interaction_id，通过 ContextVar 在当前线程的调用链内传播；跨线程边界
（ChatWorker、TTS 请求线程）由发起方捕获、线程入口恢复。

debug_log 会自动附加当前 ID，使同一次交互的模型请求、工具调用、
TTS 生成、历史保存日志可以按 interaction_id 串联定位。
"""

from __future__ import annotations

from contextvars import ContextVar

_current_interaction_id: ContextVar[str] = ContextVar("sakura_interaction_id", default="")


def set_interaction_id(interaction_id: str) -> None:
    """设置当前调用链的交互 ID；线程入口处恢复跨线程传递的 ID 时也用它。"""
    _current_interaction_id.set(str(interaction_id or ""))


def get_interaction_id() -> str:
    """返回当前调用链的交互 ID；无交互上下文时为空串。"""
    return _current_interaction_id.get()


def clear_interaction_id() -> None:
    _current_interaction_id.set("")
