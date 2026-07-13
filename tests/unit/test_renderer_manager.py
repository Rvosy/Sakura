"""RendererManager 选择 / 降级 / 事件转发单元测试。

不实例化 QtWebEngine：通过 RendererContribution 注入假渲染器，
覆盖「插件缺失、插件选择、回退、事件转发、关闭退订、异常隔离」等关键分支。
"""

from __future__ import annotations

from pathlib import Path

from app.plugins import RendererContribution, RendererCreateContext
from app.plugins.events import (
    EVENT_APP_CLOSING,
    EVENT_APP_STARTED,
    EVENT_LLM_REQUEST_FAILED,
    EVENT_LLM_REQUEST_FINISHED,
    EVENT_LLM_REQUEST_STARTED,
    EVENT_TTS_FINISHED,
    EVENT_TTS_STARTED,
    PluginEventBus,
)
from app.renderers.base import CharacterRenderer
from app.renderers.manager import RENDERER_EVENTS, RendererManager


class FakeProfile:
    def __init__(self, renderer_config, package_dir: Path = Path(".")) -> None:
        self.id = "test"
        self.display_name = "Test"
        self.package_dir = package_dir
        self.renderer_config = renderer_config


class RecordingRenderer(CharacterRenderer):
    renderer_name = "mmd"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict | None]] = []
        self.closed = False
        self.shown = False
        self.loaded = None
        self.geometry = None

    def load_character(self, character_config) -> None:
        self.loaded = character_config

    def show(self) -> None:
        self.shown = True

    def close(self) -> None:
        self.closed = True

    def set_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self.geometry = (x, y, width, height)

    def handle_event(self, event_name, payload=None) -> None:
        self.events.append((event_name, payload))


def _manager(renderer_config, bus=None, contributions=None):
    return RendererManager(
        settings_service=None,
        character_profile=FakeProfile(renderer_config),
        event_bus=bus,
        renderer_contributions=contributions or [],
    )


def _contribution(create) -> RendererContribution:
    return RendererContribution(
        renderer_type="mmd",
        display_name="MMD",
        create=create,
        plugin_id="mmd_renderer",
    )


def test_renderer_subscribes_only_to_host_emitted_events() -> None:
    assert RENDERER_EVENTS == (
        EVENT_APP_STARTED,
        EVENT_APP_CLOSING,
        EVENT_TTS_STARTED,
        EVENT_TTS_FINISHED,
        EVENT_LLM_REQUEST_STARTED,
        EVENT_LLM_REQUEST_FINISHED,
        EVENT_LLM_REQUEST_FAILED,
    )


def test_missing_plugin_renderer_uses_default():
    mgr = _manager({"type": "mmd"})
    mgr.select_and_init()
    assert mgr.active_renderer_name == "default"
    assert not mgr.is_overlay_active


def test_no_renderer_config_uses_default():
    mgr = _manager(None)
    mgr.select_and_init()
    assert mgr.active_renderer_name == "default"


def test_unknown_type_uses_default():
    mgr = _manager({"type": "live2d"})
    mgr.select_and_init()
    assert mgr.active_renderer_name == "default"


def test_plugin_renderer_selected():
    rec = RecordingRenderer()
    seen_context: list[RendererCreateContext] = []

    def create(context: RendererCreateContext):
        seen_context.append(context)
        return rec

    mgr = _manager({"type": "mmd"}, contributions=[_contribution(create)])
    mgr.select_and_init()
    assert mgr.active is rec
    assert mgr.is_overlay_active
    assert seen_context[0].character_id == "test"
    assert seen_context[0].renderer_config == {"type": "mmd"}


def test_plugin_renderer_unavailable_falls_back_to_default():
    class UnavailableRenderer(RecordingRenderer):
        def is_available(self) -> bool:
            return False

    mgr = _manager(
        {"type": "mmd", "fallback": "default"},
        contributions=[_contribution(lambda _context: UnavailableRenderer())],
    )
    mgr.select_and_init()
    assert mgr.active_renderer_name == "default"


def test_event_forwarded_to_active():
    rec = RecordingRenderer()
    bus = PluginEventBus()
    mgr = _manager({"type": "mmd"}, bus=bus, contributions=[_contribution(lambda _context: rec)])
    mgr.select_and_init()
    bus.emit(EVENT_TTS_STARTED, {"seq": 1})
    assert (EVENT_TTS_STARTED, {"seq": 1}) in rec.events


def test_geometry_forwarded_to_active():
    rec = RecordingRenderer()
    mgr = _manager({"type": "mmd"}, contributions=[_contribution(lambda _context: rec)])
    mgr.select_and_init()
    mgr.set_geometry(10, 20, 300, 400)
    assert rec.geometry == (10, 20, 300, 400)


def test_app_closing_closes_and_unsubscribes():
    rec = RecordingRenderer()
    bus = PluginEventBus()
    mgr = _manager({"type": "mmd"}, bus=bus, contributions=[_contribution(lambda _context: rec)])
    mgr.select_and_init()
    bus.emit(EVENT_APP_CLOSING, {})
    assert rec.closed
    # 退订后再次 emit 不应再转发
    rec.events.clear()
    bus.emit(EVENT_TTS_STARTED, {})
    assert rec.events == []


def test_handler_exception_isolated():
    class Boom(RecordingRenderer):
        def handle_event(self, event_name, payload=None):
            raise RuntimeError("boom")

    rec = Boom()
    bus = PluginEventBus()
    mgr = _manager({"type": "mmd"}, bus=bus, contributions=[_contribution(lambda _context: rec)])
    mgr.select_and_init()
    # 不应抛出
    bus.emit(EVENT_LLM_REQUEST_STARTED, {})


def test_plugin_renderer_create_exception_falls_back():
    def create(_context):
        raise RuntimeError("init boom")

    mgr = _manager({"type": "mmd"}, contributions=[_contribution(create)])
    mgr.select_and_init()
    assert mgr.active_renderer_name == "default"
