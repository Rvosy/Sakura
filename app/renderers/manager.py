"""app/renderers/manager.py — 角色渲染器管理器。

:class:`RendererManager` 是宿主级入口，负责：
1. 依据「角色 renderer 配置 + 插件贡献」选择渲染后端；
2. 初始化插件后端，初始化失败时**回退**到 default，绝不把异常抛给宿主；
3. 接入插件事件总线，把 TTS/LLM/APP 等事件转发给当前渲染器；
4. 对外提供统一调用入口（show/hide/play_motion/...）。

为何放在宿主而非插件：渲染器需要宿主级权限（接管角色显示生命周期、窗口
协调、事件总线接入、降级管理）。具体重后端（MMD/Live2D 等）由插件贡献，
宿主不硬编码导入。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.runtime_log import log_event
from app.plugins.events import (
    EVENT_APP_CLOSING,
    EVENT_APP_STARTED,
    EVENT_LLM_REQUEST_FAILED,
    EVENT_LLM_REQUEST_FINISHED,
    EVENT_LLM_REQUEST_STARTED,
    EVENT_TTS_FINISHED,
    EVENT_TTS_STARTED,
)
from app.plugins.models import RendererContribution, RendererCreateContext
from app.renderers.base import CharacterRenderer
from app.renderers.default import DefaultRenderer

if TYPE_CHECKING:
    from app.config.character_loader import CharacterProfile
    from app.config.settings_service import AppSettingsService
    from app.plugins.events import PluginEventBus

# 渲染器在事件总线上的归属标识；卸载时按此一次性清理全部订阅。
RENDERER_PLUGIN_ID = "__renderer__"

# 渲染器订阅并转发给后端的真实宿主事件集合。
RENDERER_EVENTS: tuple[str, ...] = (
    EVENT_APP_STARTED,
    EVENT_APP_CLOSING,
    EVENT_TTS_STARTED,
    EVENT_TTS_FINISHED,
    EVENT_LLM_REQUEST_STARTED,
    EVENT_LLM_REQUEST_FINISHED,
    EVENT_LLM_REQUEST_FAILED,
)


class RendererManager:
    """选择、初始化并驱动角色渲染后端。"""

    def __init__(
        self,
        *,
        settings_service: "AppSettingsService | None",
        character_profile: "CharacterProfile",
        event_bus: "PluginEventBus | None" = None,
        parent_window: Any | None = None,
        renderer_contributions: list[RendererContribution] | None = None,
    ) -> None:
        self._settings_service = settings_service
        self._character_profile = character_profile
        self._event_bus = event_bus
        self._parent_window = parent_window
        self._renderer_contributions = list(renderer_contributions or [])
        # 初始即给 DefaultRenderer 兜底，确保 select_and_init 之前的调用也安全。
        self._active: CharacterRenderer = DefaultRenderer()
        self._subscribed = False
        self._closed = False

    # ---- 选择与初始化 ----

    def select_and_init(self) -> CharacterRenderer:
        """根据角色配置与插件贡献选择后端并初始化；失败回退 default。"""
        self._active = self._create_active()
        log_event(
            "RendererManager",
            "已选择渲染后端",
            {
                "renderer": self._active.renderer_name,
                "character_id": getattr(self._character_profile, "id", ""),
            },
        )
        self._subscribe_events()
        return self._active

    def _create_active(self) -> CharacterRenderer:
        cfg = self._renderer_config()
        requested = str(cfg.get("type") or "default").strip().lower()
        fallback = str(cfg.get("fallback") or "default").strip().lower()

        if not requested or requested == "default":
            return DefaultRenderer()

        contribution = self._find_renderer_contribution(requested)
        if contribution is None:
            log_event(
                "RendererManager",
                "未找到匹配的插件渲染器，使用默认渲染器",
                {
                    "requested": requested,
                    "character_id": getattr(self._character_profile, "id", ""),
                },
            )
            return self._create_fallback(fallback)

        renderer = self._try_create_plugin_renderer(contribution)
        if renderer is not None:
            return renderer
        return self._create_fallback(fallback)

    def _find_renderer_contribution(self, requested: str) -> RendererContribution | None:
        """按 renderer_type 查找插件贡献；同类型理论上已由 PluginManager 拦截。"""
        matches = [
            contribution
            for contribution in self._renderer_contributions
            if str(contribution.renderer_type).strip().lower() == requested
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.priority, reverse=True)[0]

    def _try_create_plugin_renderer(
        self,
        contribution: RendererContribution,
    ) -> CharacterRenderer | None:
        """尝试创建并初始化插件渲染器；任何异常都吞掉并返回 None。"""
        try:
            renderer = contribution.create(self._create_renderer_context())
            if not isinstance(renderer, CharacterRenderer):
                raise TypeError(
                    f"插件渲染器必须继承 CharacterRenderer：{contribution.renderer_type}"
                )
            if not renderer.is_available():
                log_event(
                    "RendererManager",
                    "插件渲染器自报不可用",
                    {"renderer": contribution.renderer_type, "plugin_id": contribution.plugin_id},
                )
                return None
            renderer.initialize({"character_id": getattr(self._character_profile, "id", "")})
            return renderer
        except Exception as exc:  # noqa: BLE001 — 初始化失败必须降级而非崩溃
            log_event(
                "RendererManager",
                "插件渲染器创建异常",
                {
                    "renderer": contribution.renderer_type,
                    "plugin_id": contribution.plugin_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return None

    def _create_renderer_context(self) -> RendererCreateContext:
        package_dir = getattr(self._character_profile, "package_dir", None)
        if not isinstance(package_dir, Path):
            package_dir = Path(".").resolve()
        return RendererCreateContext(
            character_id=str(getattr(self._character_profile, "id", "")),
            character_name=str(getattr(self._character_profile, "display_name", "")),
            package_dir=package_dir,
            renderer_config=self._renderer_config(),
            owner_window=self._parent_window,
            event_bus=self._event_bus,
        )

    def _create_fallback(self, fallback: str) -> CharacterRenderer:
        # 当前回退目标只支持 default；保留参数以便未来扩展回退链。
        if fallback and fallback != "default":
            log_event(
                "RendererManager",
                "暂不支持的回退后端，改用 default",
                {"fallback": fallback},
            )
        return DefaultRenderer()

    def _renderer_config(self) -> dict[str, Any]:
        cfg = getattr(self._character_profile, "renderer_config", None)
        return cfg if isinstance(cfg, dict) else {}

    # ---- 事件接入 ----

    def _subscribe_events(self) -> None:
        if self._event_bus is None or self._subscribed:
            return
        for name in RENDERER_EVENTS:
            handler = functools.partial(self._dispatch_event, name)
            try:
                self._event_bus.on(name, handler, plugin_id=RENDERER_PLUGIN_ID)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "RendererManager",
                    "事件订阅失败",
                    {"event": name, "error": str(exc)},
                )
        self._subscribed = True

    def _dispatch_event(self, event_name: str, payload: dict[str, Any]) -> None:
        """事件总线 handler：转发给当前渲染器，并处理关闭事件。"""
        try:
            self._active.handle_event(event_name, payload)
        except Exception as exc:  # noqa: BLE001 — 渲染器异常不得影响宿主主流程
            log_event(
                "RendererManager",
                "渲染器处理事件失败",
                {"event": event_name, "error": str(exc)},
            )
        if event_name == EVENT_APP_CLOSING:
            self.close()

    # ---- 统一调用入口（全部委托当前渲染器） ----

    @property
    def active(self) -> CharacterRenderer:
        return self._active

    @property
    def active_renderer_name(self) -> str:
        return self._active.renderer_name

    @property
    def is_overlay_active(self) -> bool:
        """当前后端是否是接管显示的独立渲染器（非 default 兜底）。"""
        return self._active.renderer_name != "default"

    @property
    def replaces_default_portrait(self) -> bool:
        """当前后端是否要求宿主隐藏默认 PNG 立绘。"""
        return bool(getattr(self._active, "replaces_default_portrait", False))

    def load_character(self) -> None:
        self._safe("load_character", lambda: self._active.load_character(self._renderer_config()))

    def show(self) -> None:
        self._safe("show", self._active.show)

    def hide(self) -> None:
        self._safe("hide", self._active.hide)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._safe("close", self._active.close)
        if self._event_bus is not None and self._subscribed:
            try:
                self._event_bus.remove_plugin(RENDERER_PLUGIN_ID)
            except Exception as exc:  # noqa: BLE001
                log_event("RendererManager", "事件退订失败", {"error": str(exc)})
            self._subscribed = False

    def set_position(self, x: int, y: int) -> None:
        self._safe("set_position", lambda: self._active.set_position(x, y))

    def set_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self._safe("set_geometry", lambda: self._active.set_geometry(x, y, width, height))

    def stack_below(self, owner_window: Any, *, topmost: bool | None = None) -> None:
        self._safe("stack_below", lambda: self._active.stack_below(owner_window, topmost=topmost))

    def set_scale(self, scale: float) -> None:
        self._safe("set_scale", lambda: self._active.set_scale(scale))

    def play_motion(self, motion_name: str, loop: bool = False) -> None:
        self._safe("play_motion", lambda: self._active.play_motion(motion_name, loop))

    def stop_motion(self, motion_name: str | None = None) -> None:
        self._safe("stop_motion", lambda: self._active.stop_motion(motion_name))

    def set_expression(self, expression_name: str, weight: float = 1.0) -> None:
        self._safe("set_expression", lambda: self._active.set_expression(expression_name, weight))

    def set_lip_sync(self, value: float) -> None:
        self._safe("set_lip_sync", lambda: self._active.set_lip_sync(value))

    def look_at(self, x: float, y: float) -> None:
        self._safe("look_at", lambda: self._active.look_at(x, y))

    def handle_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        self._dispatch_event(event_name, payload or {})

    def _safe(self, label: str, func: Any) -> None:
        """统一包裹渲染器调用，隔离异常，避免影响宿主。"""
        try:
            func()
        except Exception as exc:  # noqa: BLE001
            log_event(
                "RendererManager",
                "渲染器调用失败",
                {"op": label, "renderer": self._active.renderer_name, "error": str(exc)},
            )
