"""app/plugins/services.py — 插件可访问的宿主服务门面。

为了让高级插件能做有限交互，但又不直接拿到 Sakura 内部对象（主窗口、TTS
manager、LLM client 等），这里提供一组安全的门面服务。

本轮只实现最小安全方法：默认写 debug log（空实现），并预留 ``set_backends``
注入接口（seam），宿主后续可在装配处注入真实后端。插件永远只拿到本门面，
不接触内部实例。

线程说明：事件可能在 worker 线程派发，handler 调用这些服务时也在该线程。
真实 UI 后端注入时需自行 marshal 回 UI 线程；本轮 stub 不操作 UI，无此风险。
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.debug_log import debug_log


class PluginUIService:
    """UI 相关的安全入口。"""

    def __init__(self) -> None:
        # 宿主可注入：bubble_sink(text, source) -> None
        self._bubble_sink: Callable[[str, str | None], None] | None = None

    def set_bubble_sink(self, sink: Callable[[str, str | None], None] | None) -> None:
        """注入真实气泡后端；传 None 恢复为空实现。"""
        self._bubble_sink = sink

    def show_bubble(self, text: str, *, source: str | None = None) -> None:
        """请求宿主显示一个气泡提示。未注入后端时仅写日志。"""
        try:
            if self._bubble_sink is not None:
                self._bubble_sink(text, source)
                return
            debug_log(
                "PluginUIService",
                "show_bubble（未接后端，空实现）",
                {"source": source, "text": text},
            )
        except Exception as exc:  # noqa: BLE001 — 服务调用不得影响插件或宿主
            debug_log("PluginUIService", "show_bubble 失败", {"error": str(exc)})


class PluginTTSService:
    """TTS 相关的安全入口。"""

    def __init__(self) -> None:
        # 宿主可注入：tts_sink(text, interrupt) -> None
        self._tts_sink: Callable[[str, bool], None] | None = None

    def set_tts_sink(self, sink: Callable[[str, bool], None] | None) -> None:
        """注入真实 TTS 后端；传 None 恢复为空实现。"""
        self._tts_sink = sink

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        """请求宿主朗读文本。未注入后端时仅写日志。"""
        try:
            if self._tts_sink is not None:
                self._tts_sink(text, interrupt)
                return
            debug_log(
                "PluginTTSService",
                "speak（未接后端，空实现）",
                {"interrupt": interrupt, "text": text},
            )
        except Exception as exc:  # noqa: BLE001
            debug_log("PluginTTSService", "speak 失败", {"error": str(exc)})


class PluginAgentService:
    """Agent 相关的安全入口。

    插件不能直接调用 LLM client，只能向宿主提出请求，由宿主决定是否执行。
    本轮仅记录请求，不真正触发主动回复（未来主动性插件入口）。
    """

    def __init__(self) -> None:
        # 宿主可注入：passive_reply_sink(reason, context) -> None
        self._passive_reply_sink: Callable[[str, dict[str, Any] | None], None] | None = None

    def set_passive_reply_sink(
        self,
        sink: Callable[[str, dict[str, Any] | None], None] | None,
    ) -> None:
        """注入真实主动回复后端；传 None 恢复为空实现。"""
        self._passive_reply_sink = sink

    def request_passive_reply(self, reason: str, context: dict[str, Any] | None = None) -> None:
        """向宿主请求一次被动/主动回复。本轮默认仅记录。"""
        try:
            if self._passive_reply_sink is not None:
                self._passive_reply_sink(reason, context)
                return
            debug_log(
                "PluginAgentService",
                "request_passive_reply（未接后端，仅记录）",
                {"reason": reason, "context": context or {}},
            )
        except Exception as exc:  # noqa: BLE001
            debug_log("PluginAgentService", "request_passive_reply 失败", {"error": str(exc)})


class PluginServices:
    """聚合宿主服务门面，作为 ``context.services`` 暴露给插件。"""

    def __init__(self) -> None:
        self.ui = PluginUIService()
        self.tts = PluginTTSService()
        self.agent = PluginAgentService()

    def set_backends(
        self,
        *,
        bubble_sink: Callable[[str, str | None], None] | None = None,
        tts_sink: Callable[[str, bool], None] | None = None,
        passive_reply_sink: Callable[[str, dict[str, Any] | None], None] | None = None,
    ) -> None:
        """宿主装配时一次性注入真实后端（任意项可省略）。"""
        if bubble_sink is not None:
            self.ui.set_bubble_sink(bubble_sink)
        if tts_sink is not None:
            self.tts.set_tts_sink(tts_sink)
        if passive_reply_sink is not None:
            self.agent.set_passive_reply_sink(passive_reply_sink)
