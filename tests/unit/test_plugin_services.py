"""插件宿主服务门面（PluginServices）测试，重点覆盖 input.set_input_text 链路。"""

from __future__ import annotations

from pathlib import Path
import uuid

from app.agent.tools import ToolRegistry
from app.plugins.manager import PluginManager
from app.plugins.services import PluginInputService, PluginInputTextRequest, PluginServices


def test_set_input_text_without_sink_is_noop() -> None:
    # 未注入后端时只写日志，不抛异常。
    PluginInputService().set_input_text("hello")


def test_set_input_text_invokes_injected_sink() -> None:
    service = PluginInputService()
    received: list[str] = []
    service.set_input_text_sink(received.append)
    service.set_input_text("识别结果")
    assert received == ["识别结果"]


def test_input_request_sink_receives_replace_append_insert() -> None:
    service = PluginInputService()
    received: list[PluginInputTextRequest] = []
    service.set_input_request_sink(received.append)

    service.set_input_text("替换")
    service.append_input_text("追加")
    service.insert_input_text("插入", position=2)

    assert received == [
        PluginInputTextRequest(text="替换", mode="replace"),
        PluginInputTextRequest(text="追加", mode="append"),
        PluginInputTextRequest(text="插入", mode="insert", position=2),
    ]


def test_append_without_request_sink_is_isolated() -> None:
    service = PluginInputService()
    received: list[str] = []
    service.set_input_text_sink(received.append)

    service.append_input_text("追加")

    assert received == []


def test_set_input_text_sink_exception_is_isolated() -> None:
    service = PluginInputService()

    def boom(_text: str) -> None:
        raise RuntimeError("boom")

    service.set_input_text_sink(boom)
    # sink 抛异常被门面隔离，不向插件/宿主传播。
    service.set_input_text("x")


def test_set_backends_wires_input_text_sink() -> None:
    services = PluginServices()
    received: list[str] = []
    services.set_backends(input_text_sink=received.append)
    services.input.set_input_text("从 services 进")
    assert received == ["从 services 进"]


def test_set_backends_wires_input_request_sink() -> None:
    services = PluginServices()
    received: list[PluginInputTextRequest] = []
    services.set_backends(input_request_sink=received.append)
    services.input.append_input_text("从 services 进")
    assert received == [PluginInputTextRequest(text="从 services 进", mode="append")]


def test_plugin_reaches_input_sink_end_to_end() -> None:
    """端到端：插件经 context.services.input 打到宿主注入的 sink。"""
    base = _runtime_root("input_plugin")
    plugin_dir = base / "plugins" / "input_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
api_version: 1
id: input_plugin
name: Input Plugin
entry: plugin:InputPlugin
enabled: true
permissions:
  - tool
""".lstrip(),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
from app.plugins import PluginBase

class InputPlugin(PluginBase):
    plugin_id = "input_plugin"

    def initialize(self, register, context):
        context.services.input.set_input_text("来自插件")
""".lstrip(),
        encoding="utf-8",
    )

    manager = PluginManager(base)
    received: list[str] = []
    # 宿主装配：把真实 sink 注入门面（与插件持有同一个 services 实例）。
    manager.services.set_backends(input_text_sink=received.append)

    results = manager.load_all(ToolRegistry())

    assert results[0].loaded, results[0].error
    assert received == ["来自插件"]


def _runtime_root(name: str) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
        / "test_runtime"
        / "plugin_services"
        / name
        / uuid.uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return root
