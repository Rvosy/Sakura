"""情绪状态示例插件。

演示 Sakura 插件 SDK 的两类新能力：
1. 事件订阅：通过 ``context.events.on(...)`` 监听宿主事件，收到事件时只更新
   内部状态并写日志（不做复杂逻辑）。
2. 上下文注入：通过 ``register.register_context_provider(...)`` 注册动态上下文
   提供者，每次构建 prompt 时根据本轮 ``ContextRequest`` 返回当前桌宠状态片段。

注意：这只是验证 SDK 能力的最小示例，不实现完整情绪系统。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.plugins import (
    ContextFragment,
    ContextProviderContribution,
    ContextRequest,
    PluginBase,
    PluginCapabilityRegistry,
    PluginContext,
)
from app.plugins.events import (
    EVENT_CHAT_MESSAGE_RECEIVED,
    EVENT_TTS_FINISHED,
    EVENT_USER_IDLE,
    EVENT_USER_RETURNED,
)


class EmotionStateExamplePlugin(PluginBase):
    """订阅事件并注入「当前桌宠状态」上下文的示例插件。"""

    plugin_id = "emotion_state_example"
    plugin_version = "0.1.0"

    def __init__(self) -> None:
        self.context: PluginContext | None = None
        self._mood = "平静"
        self._energy = "中等"
        self._affection = "普通"
        self._recent_event = ""
        # 事件名 -> handler，保存引用以便 shutdown 精确取消订阅。
        self._subscriptions: dict[str, Any] = {}

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        self.context = context

        # 读取配置（用户覆盖优先于安装目录默认）。
        config = context.get_config()
        self._mood = str(config.get("mood", self._mood))
        self._energy = str(config.get("energy", self._energy))
        self._affection = str(config.get("affection", self._affection))

        # 订阅宿主事件。user.idle / user.returned 本轮尚未接入真实触发点，
        # 这里仅演示订阅 API 的用法。
        events = getattr(context, "events", None)
        if events is not None:
            self._subscriptions = {
                EVENT_CHAT_MESSAGE_RECEIVED: self._on_chat_message,
                EVENT_USER_IDLE: self._on_user_idle,
                EVENT_USER_RETURNED: self._on_user_returned,
                EVENT_TTS_FINISHED: self._on_tts_finished,
            }
            for event_name, handler in self._subscriptions.items():
                events.on(event_name, handler)

        # 注册动态上下文提供者。
        register.register_context_provider(
            ContextProviderContribution(
                provider_id="emotion_state",
                description="注入当前桌宠的心情、精力与好感状态。",
                build_context=self._build_context,
                order=90.0,
            )
        )

    def shutdown(self) -> None:
        events = getattr(self.context, "events", None)
        if events is not None:
            for event_name, handler in self._subscriptions.items():
                events.off(event_name, handler)
        self._subscriptions = {}

    # ---- 上下文提供者 ----

    def _build_context(self, _request: ContextRequest) -> Sequence[ContextFragment]:
        lines = [
            "当前桌宠状态：",
            f"- 心情：{self._mood}",
            f"- 精力：{self._energy}",
            f"- 好感：{self._affection}",
        ]
        if self._recent_event:
            lines.append(f"- 最近事件：{self._recent_event}")
        # 只需提供 content；宿主会统一覆盖 id/source/trust/cache_scope 等元数据。
        return [ContextFragment(fragment_id="emotion_state", source="plugin", content="\n".join(lines))]

    # ---- 事件 handler（仅更新状态 + 写日志） ----

    def _on_chat_message(self, payload: dict[str, Any]) -> None:
        self._recent_event = "收到用户消息"
        self._log("收到 chat.message.received", {"text": payload.get("text", "")})

    def _on_user_idle(self, payload: dict[str, Any]) -> None:
        self._recent_event = "用户长时间未互动"
        self._log("收到 user.idle", payload)

    def _on_user_returned(self, payload: dict[str, Any]) -> None:
        self._recent_event = "用户回来了"
        self._log("收到 user.returned", payload)

    def _on_tts_finished(self, payload: dict[str, Any]) -> None:
        self._recent_event = "刚说完一句话"
        self._log("收到 tts.finished", {"text": payload.get("text", "")})

    def _log(self, message: str, data: dict[str, Any]) -> None:
        if self.context is not None:
            self.context.log(message, data)
