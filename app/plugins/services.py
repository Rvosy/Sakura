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

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.core.debug_log import debug_log
from app.core.resource_manager import (
    DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
    ResourceRegistry,
    ServiceResource,
    ThreadGroupResource,
)
from app.plugins.models import PERMISSION_MOBILE_CHAT


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


class PluginInputService:
    """聊天输入框相关的安全入口。

    让插件（如语音输入按钮）把文本填入用户输入框，但不直接发送，也不接触
    主窗口或输入控件本身——交由用户确认/编辑后再自行发送。
    """

    def __init__(self) -> None:
        # 宿主可注入：input_text_sink(text) -> None
        self._input_text_sink: Callable[[str], None] | None = None

    def set_input_text_sink(self, sink: Callable[[str], None] | None) -> None:
        """注入真实输入框后端；传 None 恢复为空实现。"""
        self._input_text_sink = sink

    def set_input_text(self, text: str) -> None:
        """请求宿主把文本填入聊天输入框（替换当前内容，不发送）。

        典型用途：语音识别（ASR）得到结果后填入输入框，由用户确认或编辑后发送。
        未注入后端时仅写日志。
        """
        try:
            if self._input_text_sink is not None:
                self._input_text_sink(text)
                return
            debug_log(
                "PluginInputService",
                "set_input_text（未接后端，空实现）",
                {"text": text},
            )
        except Exception as exc:  # noqa: BLE001 — 服务调用不得影响插件或宿主
            debug_log("PluginInputService", "set_input_text 失败", {"error": str(exc)})


class PluginMobileService:
    """供远程聊天入口复用宿主对话、历史与记忆的受控门面。"""

    def __init__(self) -> None:
        self._characters_sink: Callable[[], list[dict[str, str]]] | None = None
        self._history_sink: Callable[[str, int], list[dict[str, str]]] | None = None
        self._chat_sink: Callable[[str, str, str], dict[str, Any]] | None = None
        self._theme_sink: Callable[[], dict[str, object]] | None = None

    def set_backends(
        self,
        *,
        characters_sink: Callable[[], list[dict[str, str]]] | None = None,
        history_sink: Callable[[str, int], list[dict[str, str]]] | None = None,
        chat_sink: Callable[[str, str, str], dict[str, Any]] | None = None,
        theme_sink: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        if characters_sink is not None:
            self._characters_sink = characters_sink
        if history_sink is not None:
            self._history_sink = history_sink
        if chat_sink is not None:
            self._chat_sink = chat_sink
        if theme_sink is not None:
            self._theme_sink = theme_sink

    def characters(self) -> list[dict[str, str]]:
        if self._characters_sink is None:
            raise RuntimeError("移动端聊天服务尚未就绪。")
        return self._characters_sink()

    def history(self, character_id: str, *, limit: int = 50) -> list[dict[str, str]]:
        if self._history_sink is None:
            raise RuntimeError("移动端聊天服务尚未就绪。")
        return self._history_sink(character_id, limit)

    def chat(self, character_id: str, text: str, image_data_url: str = "") -> dict[str, Any]:
        if self._chat_sink is None:
            raise RuntimeError("移动端聊天服务尚未就绪。")
        return self._chat_sink(character_id, text, image_data_url)

    def theme(self) -> dict[str, object]:
        if self._theme_sink is None:
            return _default_theme_mapping()
        try:
            return _theme_mapping(self._theme_sink())
        except Exception as exc:  # noqa: BLE001
            debug_log("PluginMobileService", "读取移动端主题失败，使用默认主题", {"error": str(exc)})
            return _default_theme_mapping()


class PluginResourceService:
    """插件可登记的资源门面，由宿主 ResourceRegistry 统一关闭。"""

    def __init__(self, registry: ResourceRegistry | None = None) -> None:
        self._registry = registry or ResourceRegistry()
        self._lock = threading.RLock()
        self._plugin_resources: dict[str, list[Any]] = {}

    def set_resource_registry(self, registry: ResourceRegistry) -> None:
        """宿主装配 shared registry；应在插件加载前调用。"""
        with self._lock:
            self._registry = registry

    def for_plugin(self, plugin_id: str) -> "ScopedPluginResourceService":
        """返回绑定单个插件 ID 的窄资源门面。"""
        return ScopedPluginResourceService(self, plugin_id)

    def register_cleanup(
        self,
        plugin_id: str,
        cleanup: Callable[[], Any],
        *,
        label: str = "cleanup",
        shutdown_order: int = 650,
    ) -> ServiceResource:
        """登记插件清理函数，关闭时由 registry 调度。"""
        resource = self._registry.track_service(
            stop=cleanup,
            label=self._resource_label(plugin_id, label),
            shutdown_order=shutdown_order,
        )
        self._remember(plugin_id, resource)
        return resource

    def track_thread_group(
        self,
        plugin_id: str,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "threads",
        shutdown_order: int = 700,
    ) -> ThreadGroupResource:
        """为插件创建受管线程组，插件只拿到线程组资源本身。"""
        resource = self._registry.track_thread_group(
            cancel=cancel,
            label=self._resource_label(plugin_id, label),
            shutdown_order=shutdown_order,
        )
        self._remember(plugin_id, resource)
        return resource

    def register_executor(
        self,
        plugin_id: str,
        executor: ThreadPoolExecutor,
        *,
        label: str = "executor",
        shutdown_order: int = 700,
    ) -> ServiceResource:
        """登记 ThreadPoolExecutor，关闭时取消未开始任务并不阻塞 UI。"""

        def shutdown_executor() -> None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

        return self.register_cleanup(
            plugin_id,
            shutdown_executor,
            label=label,
            shutdown_order=shutdown_order,
        )

    def stop_plugin(
        self,
        plugin_id: str,
        timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
    ) -> None:
        """停止某个插件登记过的全部资源；幂等。"""
        with self._lock:
            resources = list(reversed(self._plugin_resources.pop(plugin_id, [])))
        for resource in resources:
            stop = getattr(resource, "stop", None)
            if not callable(stop):
                continue
            try:
                stop(timeout_ms)
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "PluginResourceService",
                    "插件资源关闭失败",
                    {"plugin_id": plugin_id, "error": str(exc)},
                )

    def _remember(self, plugin_id: str, resource: Any) -> None:
        with self._lock:
            self._plugin_resources.setdefault(plugin_id, []).append(resource)

    @staticmethod
    def _resource_label(plugin_id: str, label: str) -> str:
        label_text = str(label or "resource").strip() or "resource"
        return f"plugin:{plugin_id}:{label_text}"


class ScopedPluginResourceService:
    """绑定插件 ID 后暴露给插件的资源登记接口。"""

    def __init__(self, parent: PluginResourceService, plugin_id: str) -> None:
        self._parent = parent
        self._plugin_id = plugin_id

    def register_cleanup(
        self,
        cleanup: Callable[[], Any],
        *,
        label: str = "cleanup",
        shutdown_order: int = 650,
    ) -> ServiceResource:
        return self._parent.register_cleanup(
            self._plugin_id,
            cleanup,
            label=label,
            shutdown_order=shutdown_order,
        )

    def track_thread_group(
        self,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "threads",
        shutdown_order: int = 700,
    ) -> ThreadGroupResource:
        return self._parent.track_thread_group(
            self._plugin_id,
            cancel=cancel,
            label=label,
            shutdown_order=shutdown_order,
        )

    def register_executor(
        self,
        executor: ThreadPoolExecutor,
        *,
        label: str = "executor",
        shutdown_order: int = 700,
    ) -> ServiceResource:
        return self._parent.register_executor(
            self._plugin_id,
            executor,
            label=label,
            shutdown_order=shutdown_order,
        )


class ScopedPluginServices:
    """单插件视角的宿主服务集合。"""

    def __init__(
        self,
        services: "PluginServices",
        plugin_id: str,
        permissions: tuple[str, ...] = (),
    ) -> None:
        self.ui = services.ui
        self.tts = services.tts
        self.agent = services.agent
        self.input = services.input
        self.mobile = services.mobile if PERMISSION_MOBILE_CHAT in permissions else None
        self.resources = services.resources.for_plugin(plugin_id)


class PluginServices:
    """聚合宿主服务门面，作为 ``context.services`` 暴露给插件。"""

    def __init__(self) -> None:
        self.ui = PluginUIService()
        self.tts = PluginTTSService()
        self.agent = PluginAgentService()
        self.input = PluginInputService()
        self.mobile = PluginMobileService()
        self.resources = PluginResourceService()

    def set_backends(
        self,
        *,
        bubble_sink: Callable[[str, str | None], None] | None = None,
        tts_sink: Callable[[str, bool], None] | None = None,
        passive_reply_sink: Callable[[str, dict[str, Any] | None], None] | None = None,
        input_text_sink: Callable[[str], None] | None = None,
        mobile_characters_sink: Callable[[], list[dict[str, str]]] | None = None,
        mobile_history_sink: Callable[[str, int], list[dict[str, str]]] | None = None,
        mobile_chat_sink: Callable[[str, str, str], dict[str, Any]] | None = None,
        mobile_theme_sink: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        """宿主装配时一次性注入真实后端（任意项可省略）。"""
        if bubble_sink is not None:
            self.ui.set_bubble_sink(bubble_sink)
        if tts_sink is not None:
            self.tts.set_tts_sink(tts_sink)
        if passive_reply_sink is not None:
            self.agent.set_passive_reply_sink(passive_reply_sink)
        if input_text_sink is not None:
            self.input.set_input_text_sink(input_text_sink)
        self.mobile.set_backends(
            characters_sink=mobile_characters_sink,
            history_sink=mobile_history_sink,
            chat_sink=mobile_chat_sink,
            theme_sink=mobile_theme_sink,
        )

    def set_resource_registry(self, registry: ResourceRegistry) -> None:
        """宿主注入 App 级资源域。"""
        self.resources.set_resource_registry(registry)

    def for_plugin(
        self,
        plugin_id: str,
        permissions: tuple[str, ...] = (),
    ) -> ScopedPluginServices:
        """构造绑定插件 ID 的服务视图，避免插件看到全局资源门面。"""
        return ScopedPluginServices(self, plugin_id, permissions)


def _default_theme_mapping() -> dict[str, object]:
    from app.ui.theme import DEFAULT_THEME_SETTINGS, theme_colors_to_mapping

    return theme_colors_to_mapping(DEFAULT_THEME_SETTINGS)


def _theme_mapping(data: Any) -> dict[str, object]:
    from app.ui.theme import theme_colors_to_mapping, theme_from_mapping

    return theme_colors_to_mapping(theme_from_mapping(data))
