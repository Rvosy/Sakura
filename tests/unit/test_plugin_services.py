"""插件宿主服务门面（PluginServices）测试，重点覆盖 input.set_input_text 链路。"""

from __future__ import annotations

from pathlib import Path
import uuid

from app.agent.tools import ToolRegistry
from app.plugins.models import PERMISSION_MOBILE_CHAT
from app.plugins.manager import PluginManager
from app.plugins.services import PluginInputService, PluginServices
from app.core.resource_manager import ResourceRegistry
from app.ui.theme import DEFAULT_THEME_SETTINGS, theme_colors_to_mapping


def test_set_input_text_without_sink_is_noop() -> None:
    # 未注入后端时只写日志，不抛异常。
    PluginInputService().set_input_text("hello")


def test_set_input_text_invokes_injected_sink() -> None:
    service = PluginInputService()
    received: list[str] = []
    service.set_input_text_sink(received.append)
    service.set_input_text("识别结果")
    assert received == ["识别结果"]


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


def test_mobile_theme_defaults_to_sakura_theme() -> None:
    assert PluginServices().mobile.theme() == theme_colors_to_mapping(DEFAULT_THEME_SETTINGS)


def test_set_backends_wires_mobile_theme_sink() -> None:
    services = PluginServices()
    services.set_backends(
        mobile_theme_sink=lambda: {
            "primary_color": "#112233",
            "accent_color": "#445566",
        }
    )

    theme = services.mobile.theme()

    assert theme["primary_color"] == "#112233"
    assert theme["accent_color"] == "#445566"
    assert theme["text_color"] == DEFAULT_THEME_SETTINGS.text_color


def test_mobile_service_requires_permission() -> None:
    services = PluginServices()

    assert services.for_plugin("plain").mobile is None
    assert services.for_plugin("mobile", (PERMISSION_MOBILE_CHAT,)).mobile is services.mobile


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


def test_plugin_resource_cleanup_runs_from_shared_registry() -> None:
    registry = ResourceRegistry()
    services = PluginServices()
    services.set_resource_registry(registry)
    calls: list[str] = []

    services.for_plugin("demo").resources.register_cleanup(
        lambda: calls.append("cleanup"),
        label="cleanup",
    )

    registry.stop_all()

    assert calls == ["cleanup"]


def test_plugin_manager_shutdown_all_stops_resources_once() -> None:
    base = _runtime_root("resource_plugin")
    plugin_dir = base / "plugins" / "resource_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
api_version: 1
id: resource_plugin
name: Resource Plugin
entry: plugin:ResourcePlugin
enabled: true
permissions:
  - tool
""".lstrip(),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
from app.plugins import PluginBase

class ResourcePlugin(PluginBase):
    plugin_id = "resource_plugin"

    def initialize(self, register, context):
        self.log_path = context.data_dir / "lifecycle.txt"
        context.services.resources.register_cleanup(self._cleanup, label="cleanup")

    def _append(self, text):
        existing = self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else ""
        self.log_path.write_text(existing + text + "\\n", encoding="utf-8")

    def _cleanup(self):
        self._append("cleanup")

    def shutdown(self):
        self._append("shutdown")
""".lstrip(),
        encoding="utf-8",
    )

    manager = PluginManager(base)
    results = manager.load_all(ToolRegistry())

    assert results[0].loaded, results[0].error
    manager.shutdown_all()
    manager.shutdown_all()

    log_path = base / "data" / "plugins" / "resource_plugin" / "lifecycle.txt"
    assert log_path.read_text(encoding="utf-8").splitlines() == ["cleanup", "shutdown"]


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
