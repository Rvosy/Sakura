"""app/plugins/events.py — Sakura 插件轻量事件总线。

与 ``PluginManager.emit_event`` 的旧 hook 机制并存：
- 旧机制按权限把固定生命周期事件派发给插件的 ``on_xxx`` 方法。
- 本模块提供「字符串事件名 + handler 订阅」的通用事件总线，插件通过
  ``context.events.on(event_name, handler)`` 订阅，宿主在关键时机 ``emit``。

设计要点：
- 插件只能拿到 :class:`ScopedEventBus`（只暴露 ``on`` / ``off``），不能 ``emit``，
  避免插件随意发起高权限事件。
- 单个 handler 异常被隔离，不影响其他 handler，也不影响宿主主流程。
- 每次派发写 debug log，便于排查。
- 插件卸载时由 :meth:`PluginEventBus.remove_plugin` 清理其全部订阅，不残留 handler。
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.debug_log import debug_log

# 事件 payload 统一为 dict；handler 接收单个 payload 参数。
EventHandler = Callable[[dict[str, Any]], None]


# ---- 预定义事件名常量（集中管理，避免散落字符串） ----

# 应用生命周期
EVENT_APP_STARTED = "app.started"
EVENT_APP_CLOSING = "app.closing"

# 聊天消息
EVENT_CHAT_MESSAGE_RECEIVED = "chat.message.received"
EVENT_CHAT_MESSAGE_SENT = "chat.message.sent"

# LLM 请求
EVENT_LLM_REQUEST_STARTED = "llm.request.started"
EVENT_LLM_REQUEST_FINISHED = "llm.request.finished"
EVENT_LLM_REQUEST_FAILED = "llm.request.failed"

# 工具调用
EVENT_TOOL_STARTED = "tool.started"
EVENT_TOOL_FINISHED = "tool.finished"
EVENT_TOOL_FAILED = "tool.failed"

# TTS
EVENT_TTS_STARTED = "tts.started"
EVENT_TTS_FINISHED = "tts.finished"
EVENT_TTS_FAILED = "tts.failed"

# 以下事件名已预留常量，但本轮尚未接入真实触发点，后续待接入：
# 桌宠交互
EVENT_PET_CLICKED = "pet.clicked"  # TODO: 后续接入桌宠点击触发点
EVENT_PET_DRAGGED = "pet.dragged"  # TODO: 后续接入桌宠拖拽触发点
EVENT_PET_HIDDEN = "pet.hidden"  # TODO: 后续接入桌宠隐藏触发点
EVENT_PET_REOPENED = "pet.reopened"  # TODO: 后续接入桌宠重新打开触发点

# 用户活跃状态
EVENT_USER_IDLE = "user.idle"  # TODO: 后续接入用户空闲检测
EVENT_USER_RETURNED = "user.returned"  # TODO: 后续接入用户返回检测

# 屏幕感知
EVENT_SCREEN_CHANGED = "screen.changed"  # TODO: 后续接入屏幕变化检测
EVENT_SCREEN_SUMMARY_UPDATED = "screen.summary.updated"  # TODO: 后续接入屏幕摘要更新
EVENT_SCREEN_ERROR_DETECTED = "screen.error_detected"  # TODO: 后续接入屏幕异常检测

# Agent 思考
EVENT_AGENT_THINKING_STARTED = "agent.thinking.started"  # TODO: 后续接入 Agent 思考开始
EVENT_AGENT_THINKING_FINISHED = "agent.thinking.finished"  # TODO: 后续接入 Agent 思考结束


class PluginEventBus:
    """宿主持有的插件事件总线。

    插件不直接持有本对象，而是通过 :class:`ScopedEventBus` 间接订阅，
    以便宿主按插件维度统一清理。
    """

    def __init__(self) -> None:
        # event_name -> [(plugin_id, handler), ...]
        self._handlers: dict[str, list[tuple[str | None, EventHandler]]] = {}

    def on(
        self,
        event_name: str,
        handler: EventHandler,
        *,
        plugin_id: str | None = None,
    ) -> None:
        """订阅事件。plugin_id 用于卸载时按插件清理。"""
        if not callable(handler):
            raise TypeError("事件 handler 必须可调用")
        self._handlers.setdefault(event_name, []).append((plugin_id, handler))
        debug_log(
            "PluginEventBus",
            "订阅事件",
            {"event": event_name, "plugin_id": plugin_id},
        )

    def off(self, event_name: str, handler: EventHandler) -> None:
        """取消订阅指定事件上的某个 handler。

        使用 ``==`` 比较而非 ``is``：绑定方法每次取属性都会生成新的对象
        （``obj.m is obj.m`` 为 False），但其相等比较会按 ``__self__`` /
        ``__func__`` 判定，普通函数与 lambda 的相等比较则退化为身份比较。
        """
        entries = self._handlers.get(event_name)
        if not entries:
            return
        self._handlers[event_name] = [
            entry for entry in entries if entry[1] != handler
        ]
        if not self._handlers[event_name]:
            del self._handlers[event_name]

    def emit(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """派发事件给所有订阅者；单个 handler 异常被隔离。"""
        entries = list(self._handlers.get(event_name, ()))
        if not entries:
            return
        data = payload or {}
        debug_log(
            "PluginEventBus",
            "派发事件",
            {"event": event_name, "handlers": len(entries)},
        )
        for plugin_id, handler in entries:
            try:
                handler(data)
            except Exception as exc:  # noqa: BLE001 — handler 异常不得影响其他插件或宿主
                debug_log(
                    "PluginEventBus",
                    "事件 handler 失败",
                    {"event": event_name, "plugin_id": plugin_id, "error": str(exc)},
                )

    def remove_plugin(self, plugin_id: str) -> None:
        """移除某插件的全部订阅，确保卸载后不残留 handler。"""
        if not plugin_id:
            return
        for event_name in list(self._handlers.keys()):
            self._handlers[event_name] = [
                entry for entry in self._handlers[event_name] if entry[0] != plugin_id
            ]
            if not self._handlers[event_name]:
                del self._handlers[event_name]

    def handler_count(self, event_name: str | None = None) -> int:
        """返回订阅数（用于测试与调试）。"""
        if event_name is not None:
            return len(self._handlers.get(event_name, ()))
        return sum(len(entries) for entries in self._handlers.values())


class ScopedEventBus:
    """暴露给单个插件的事件门面。

    只提供 ``on`` / ``off``，不提供 ``emit``：本轮只开放订阅，
    事件发起仍由宿主统一负责。
    """

    def __init__(self, bus: PluginEventBus, plugin_id: str) -> None:
        self._bus = bus
        self._plugin_id = plugin_id

    def on(self, event_name: str, handler: EventHandler) -> None:
        """订阅宿主事件。"""
        self._bus.on(event_name, handler, plugin_id=self._plugin_id)

    def off(self, event_name: str, handler: EventHandler) -> None:
        """取消订阅。"""
        self._bus.off(event_name, handler)
