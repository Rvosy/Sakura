"""tests/unit/test_plugin_system.py — 插件系统测试。

覆盖：
- PluginDiscovery 发现/解析
- PluginCapabilityRegistry 贡献收集
- PluginManager 加载/失败隔离/优先级
- PluginLoadResult
- PluginManifest / PluginSpec
"""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from app.agent.tools import Tool, ToolRegistry
from app.plugins import (
    PluginCapabilityRegistry,
    PluginDiscovery,
    PluginLoadResult,
    PluginManager,
    PluginManifest,
    PluginSpec,
)
from app.plugins.models import (
    PERMISSION_CHAT_UI,
    PERMISSION_EVENT_MESSAGE,
    PERMISSION_PROMPT_PATCH,
    PERMISSION_RENDERER,
    PERMISSION_SETTINGS_PANEL,
    PERMISSION_TOOL,
    PERMISSION_TOOLS_TAB,
    ChatUIWidgetContribution,
    PromptPatchContribution,
    RendererContribution,
    SettingsPanelContribution,
    ToolContribution,
    ToolsTabContribution,
)
from app.plugins.discovery import save_plugin_enabled_overrides


class TestPluginSpec:
    """PluginSpec 数据模型"""

    def test_basic_spec(self) -> None:
        spec = PluginSpec(entry="test.module:TestPlugin")
        assert spec.entry == "test.module:TestPlugin"
        assert spec.enabled is True
        assert spec.priority == 100

    def test_spec_with_priority(self) -> None:
        spec = PluginSpec(entry="test:Test", priority=50, enabled=False)
        assert spec.priority == 50
        assert not spec.enabled


class TestPluginManifest:
    """PluginManifest 数据模型"""

    def test_basic_manifest(self) -> None:
        m = PluginManifest(plugin_id="test", version="1.0")
        assert m.plugin_id == "test"
        assert m.version == "1.0"
        assert m.priority == 100
        assert m.enabled is True
        assert m.required is False


class TestPluginCapabilityRegistry:
    """能力注册表"""

    def test_register_tool(self) -> None:
        reg = PluginCapabilityRegistry()
        reg.register_tool(ToolContribution(name="t1", description="d", parameters={}, handler=None))
        assert len(reg.tools) == 1

    def test_register_multiple_types(self) -> None:
        reg = PluginCapabilityRegistry()
        reg.register_tool(ToolContribution(name="t1", description="d", parameters={}, handler=None))
        reg.register_tools_tab(ToolsTabContribution(tab_id="tab", title="T", build=lambda p: None))
        reg.register_settings_panel(SettingsPanelContribution(section_id="s", title="S", build=lambda p: None))
        reg.register_chat_ui_widget(ChatUIWidgetContribution(widget_id="w", build=lambda p: None))
        reg.register_prompt_patch(PromptPatchContribution(patch_id="p", system_prompt_append="append"))
        assert len(reg.tools) == 1
        assert len(reg.tools_tabs) == 1
        assert len(reg.settings_panels) == 1
        assert len(reg.chat_ui_widgets) == 1
        assert len(reg.prompt_patches) == 1

    def test_register_renderer(self) -> None:
        reg = PluginCapabilityRegistry()
        reg.register_renderer(
            RendererContribution(
                renderer_type="mmd",
                display_name="MMD",
                create=lambda context: None,
            )
        )
        assert len(reg.renderers) == 1
        assert reg.renderers[0].renderer_type == "mmd"

    def test_empty_registry(self) -> None:
        reg = PluginCapabilityRegistry()
        assert len(reg.tools) == 0
        assert len(reg.tools_tabs) == 0

    def test_tool_decorator_registers_without_global_state(self) -> None:
        reg = PluginCapabilityRegistry()

        @reg.tool(name="decorated_tool", description="decorated")
        def decorated(value: str) -> dict[str, str]:
            return {"value": value}

        assert decorated("x") == {"value": "x"}
        assert len(reg.tools) == 1
        assert reg.tools[0].parameters["required"] == ["value"]
        assert reg.tools[0].handler({"value": "ok"}) == {"value": "ok"}  # type: ignore[misc]


class TestPluginDiscovery:
    """插件发现"""

    def test_empty_discover(self) -> None:
        base = _runtime_root("empty_discover")
        (base / "data" / "config").mkdir(parents=True)
        discovery = PluginDiscovery(base)
        specs = discovery.discover()
        assert specs == []

    def test_discover_manifest_with_config_override(self) -> None:
        base = _runtime_root("manifest_override")
        _write_plugin_manifest(base, "demo", priority=40)
        config_dir = base / "data" / "config"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("plugins.yaml").write_text(
            """
- id: demo
  enabled: false
  priority: 200
""".strip(),
            encoding="utf-8",
        )
        discovery = PluginDiscovery(base)
        specs = discovery.discover()
        assert len(specs) == 1
        assert specs[0].plugin_id == "demo"
        assert specs[0].entry == "plugin:DemoPlugin"
        assert specs[0].author == "Demo Author"
        assert specs[0].description == "demo 插件介绍"
        assert specs[0].priority == 200
        assert specs[0].enabled is False
        assert discovery.discover_enabled() == []

    def test_save_plugin_enabled_overrides(self) -> None:
        base = _runtime_root("save_enabled_overrides")
        _write_plugin_manifest(base, "demo", priority=40)

        changed = save_plugin_enabled_overrides(base, {"demo": False})
        specs = PluginDiscovery(base).discover()

        assert changed
        assert specs[0].plugin_id == "demo"
        assert specs[0].enabled is False

    def test_config_entry_items_are_ignored(self) -> None:
        base = _runtime_root("entry_config_ignored")
        config_dir = base / "data" / "config"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("plugins.yaml").write_text(
            """
- entry: plugins.a:PluginA
  enabled: true
  priority: 200
- entry: plugins.b:PluginB
  enabled: false
  priority: 50
""".strip(),
            encoding="utf-8",
        )
        discovery = PluginDiscovery(base)
        specs = discovery.discover()
        assert specs == []

    def test_discover_enabled_only(self) -> None:
        base = _runtime_root("enabled_only")
        _write_plugin_manifest(base, "a", priority=200)
        _write_plugin_manifest(base, "b", priority=100)
        config_dir = base / "data" / "config"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("plugins.yaml").write_text("""
- id: a
  enabled: true
- id: b
  enabled: false
""")
        discovery = PluginDiscovery(base)
        enabled = discovery.discover_enabled()
        assert len(enabled) == 1
        assert enabled[0].plugin_id == "a"


class TestPluginManager:
    """插件管理器"""

    def test_load_all_no_plugins(self) -> None:
        base = _runtime_root("no_plugins")
        (base / "data" / "config").mkdir(parents=True)
        mgr = PluginManager(base)
        results = mgr.load_all()
        assert results == []
        assert mgr.loaded_count == 0
        assert mgr.failed_count == 0

    def test_collect_tools_empty(self) -> None:
        base = _runtime_root("empty_tools")
        (base / "data" / "config").mkdir(parents=True)
        mgr = PluginManager(base)
        mgr.load_all()
        tools = mgr.collect_tools()
        assert tools == []

    def test_collect_tools_tabs_empty(self) -> None:
        base = _runtime_root("empty_tabs")
        (base / "data" / "config").mkdir(parents=True)
        mgr = PluginManager(base)
        mgr.load_all()
        tabs = mgr.collect_tools_tabs()
        assert tabs == []

    def test_loads_manifest_plugin_and_registers_all_contributions(self) -> None:
        base = _runtime_root("load_manifest")
        _write_demo_plugin(base)
        registry = ToolRegistry()
        mgr = PluginManager(base)

        results = mgr.load_all(registry)

        assert results[0].loaded
        assert results[0].manifest is not None
        assert results[0].manifest.author == "Demo Author"
        assert registry.get("demo_echo") is not None
        assert registry.execute("demo_echo", {"text": "hi"}).content == {"text": "hi"}
        assert [tab.title for tab in mgr.tools_tabs] == ["Demo 工具"]
        assert [panel.title for panel in mgr.settings_panels] == ["Demo 设置"]
        assert [panel.plugin_id for panel in mgr.settings_panels] == ["demo"]
        assert [widget.widget_id for widget in mgr.chat_ui_widgets] == ["demo_widget"]
        assert mgr.prompt_patches[0].system_prompt_append == "demo system"

    def test_duplicate_tool_name_marks_plugin_failed(self) -> None:
        base = _runtime_root("duplicate_tool")
        _write_demo_plugin(base, tool_name="existing_tool")
        registry = ToolRegistry([Tool(name="existing_tool", description="builtin")])
        mgr = PluginManager(base)

        results = mgr.load_all(registry)

        assert not results[0].loaded
        assert "重复" in str(results[0].error)
        assert mgr.failed_count == 1

    def test_missing_permissions_marks_plugin_failed(self) -> None:
        base = _runtime_root("missing_permissions")
        _write_demo_plugin(base, permissions=None)
        mgr = PluginManager(base)

        results = mgr.load_all()

        assert not results[0].loaded
        assert "permissions" in str(results[0].error)

    def test_unknown_permission_marks_plugin_failed(self) -> None:
        base = _runtime_root("unknown_permission")
        _write_demo_plugin(base, permissions=("tool", "unknown.permission"))
        mgr = PluginManager(base)

        results = mgr.load_all()

        assert not results[0].loaded
        assert "未知权限" in str(results[0].error)

    def test_missing_capability_permission_marks_plugin_failed(self) -> None:
        base = _runtime_root("missing_capability_permission")
        _write_demo_plugin(base, permissions=(PERMISSION_TOOL,))
        mgr = PluginManager(base)

        results = mgr.load_all()

        assert not results[0].loaded
        assert "tools_tab" in str(results[0].error)

    def test_renderer_contribution_loads_with_permission(self) -> None:
        base = _runtime_root("renderer_contribution")
        _write_renderer_plugin(base, "mmd_renderer", renderer_type="mmd")
        mgr = PluginManager(base)

        results = mgr.load_all()

        assert results[0].loaded, results[0].error
        assert [renderer.renderer_type for renderer in mgr.collect_renderers()] == ["mmd"]
        assert mgr.collect_renderers()[0].plugin_id == "mmd_renderer"

    def test_renderer_contribution_without_permission_fails(self) -> None:
        base = _runtime_root("renderer_no_permission")
        _write_renderer_plugin(
            base,
            "mmd_renderer",
            renderer_type="mmd",
            permissions=(PERMISSION_TOOL,),
        )
        mgr = PluginManager(base)

        results = mgr.load_all()

        assert not results[0].loaded
        assert "renderer" in str(results[0].error)

    def test_duplicate_renderer_type_marks_plugin_failed(self) -> None:
        base = _runtime_root("duplicate_renderer")
        _write_renderer_plugin(base, "renderer_a", renderer_type="mmd", priority=200)
        _write_renderer_plugin(base, "renderer_b", renderer_type="mmd", priority=100)
        mgr = PluginManager(base)

        results = mgr.load_all()

        assert results[0].loaded
        assert not results[1].loaded
        assert "渲染器类型重复" in str(results[1].error)

    def test_plugin_failure_isolated_from_later_plugin(self) -> None:
        base = _runtime_root("failure_isolation")
        _write_failing_plugin(base, "bad", priority=200)
        _write_demo_plugin(base, plugin_id="good", tool_name="good_echo", priority=100)
        registry = ToolRegistry()
        mgr = PluginManager(base)

        results = mgr.load_all(registry)

        assert len(results) == 2
        assert results[0].error == "boom"
        assert results[1].loaded
        assert registry.get("good_echo") is not None

    def test_required_plugin_failure_stops_loading(self) -> None:
        base = _runtime_root("required_failure")
        _write_failing_plugin(base, "bad", priority=200, required=True)
        _write_demo_plugin(base, plugin_id="good", tool_name="good_echo", priority=100)
        registry = ToolRegistry()
        mgr = PluginManager(base)

        results = mgr.load_all(registry)

        assert len(results) == 1
        assert results[0].error == "boom"
        assert registry.get("good_echo") is None

    def test_shutdown_all_uses_reverse_load_order(self) -> None:
        base = _runtime_root("shutdown_order")
        _write_shutdown_plugin(base, "first", priority=200)
        _write_shutdown_plugin(base, "second", priority=100)
        mgr = PluginManager(base)
        mgr.load_all()

        mgr.shutdown_all()

        order_file = base / "shutdown_order.txt"
        assert order_file.read_text(encoding="utf-8").splitlines() == ["second", "first"]

    def test_emit_event_calls_permitted_hook(self) -> None:
        base = _runtime_root("event_hook")
        _write_event_plugin(base, "eventful", raise_hook=False)
        mgr = PluginManager(base)
        mgr.load_all()

        mgr.emit_event("message.user", {"text": "hi"}, source="test")

        event_file = base / "eventful_events.txt"
        assert event_file.read_text(encoding="utf-8").splitlines() == ["message.user:hi:test"]

    def test_emit_event_isolates_hook_failure(self) -> None:
        base = _runtime_root("event_hook_failure")
        _write_event_plugin(base, "bad_eventful", raise_hook=True)
        mgr = PluginManager(base)
        mgr.load_all()

        mgr.emit_event("message.user", {"text": "hi"}, source="test")

        assert mgr.loaded_count == 1

    def test_plugin_load_result(self) -> None:
        spec = PluginSpec(entry="test:Test")
        result = PluginLoadResult(spec=spec, error="load failed")
        assert not result.loaded
        assert result.error == "load failed"

    def test_plugin_load_result_success(self) -> None:
        spec = PluginSpec(entry="test:Test")
        manifest = PluginManifest(plugin_id="test")
        result = PluginLoadResult(spec=spec, manifest=manifest, loaded=True)
        assert result.loaded
        assert result.manifest is not None


class TestContributionTypes:
    """贡献点数据模型"""

    def test_tool_contribution(self) -> None:
        tc = ToolContribution(name="test", description="desc", parameters={},
                              handler=None, group="memory", risk="medium",
                              requires_confirmation=True, capability="memory")
        assert tc.name == "test"
        assert tc.group == "memory"
        assert tc.risk == "medium"
        assert tc.requires_confirmation

    def test_settings_panel_contribution(self) -> None:
        sp = SettingsPanelContribution(section_id="test", title="Test Panel",
                                       build=lambda p: None, order=50.0)
        assert sp.section_id == "test"
        assert sp.order == 50.0

    def test_prompt_patch_contribution(self) -> None:
        pp = PromptPatchContribution(patch_id="p1", system_prompt_append="extra prompt")
        assert pp.patch_id == "p1"
        assert pp.system_prompt_append == "extra prompt"

    def test_renderer_contribution(self) -> None:
        rc = RendererContribution(
            renderer_type="mmd",
            display_name="MMD",
            create=lambda context: None,
            priority=50.0,
        )
        assert rc.renderer_type == "mmd"
        assert rc.display_name == "MMD"
        assert rc.priority == 50.0


def _runtime_root(name: str) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
        / "test_runtime"
        / "plugin_system"
        / name
        / uuid.uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_plugin_manifest(
    base: Path,
    plugin_id: str,
    *,
    priority: int = 100,
    required: bool = False,
    permissions: tuple[str, ...] | None = (
        PERMISSION_TOOL,
        PERMISSION_TOOLS_TAB,
        PERMISSION_SETTINGS_PANEL,
        PERMISSION_CHAT_UI,
        PERMISSION_PROMPT_PATCH,
    ),
) -> Path:
    plugin_dir = base / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (base / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    permissions_text = ""
    if permissions is not None:
        permissions_text = "\npermissions:\n" + "\n".join(
            f"  - {permission}" for permission in permissions
        )
    (plugin_dir / "plugin.yaml").write_text(
        f"""
api_version: 1
id: {plugin_id}
name: {plugin_id}
author: Demo Author
description: demo 插件介绍
version: 1.0.0
entry: plugin:DemoPlugin
enabled: true
priority: {priority}
required: {str(required).lower()}
{permissions_text}
""".strip(),
        encoding="utf-8",
    )
    return plugin_dir


def _write_demo_plugin(
    base: Path,
    *,
    plugin_id: str = "demo",
    tool_name: str = "demo_echo",
    priority: int = 100,
    permissions: tuple[str, ...] | None = (
        PERMISSION_TOOL,
        PERMISSION_TOOLS_TAB,
        PERMISSION_SETTINGS_PANEL,
        PERMISSION_CHAT_UI,
        PERMISSION_PROMPT_PATCH,
    ),
) -> None:
    plugin_dir = _write_plugin_manifest(
        base,
        plugin_id,
        priority=priority,
        permissions=permissions,
    )
    plugin_dir.joinpath("plugin.py").write_text(
        f'''
from app.plugins import PluginBase
from app.plugins import (
    ChatUIWidgetContribution,
    PromptPatchContribution,
    SettingsPanelContribution,
    ToolContribution,
    ToolsTabContribution,
)


class DemoPlugin(PluginBase):
    plugin_id = "{plugin_id}"
    plugin_version = "1.0.0"

    def initialize(self, register, context):
        register.register_tool(ToolContribution(
            name="{tool_name}",
            description="echo",
            parameters={{"type": "object", "properties": {{"text": {{"type": "string"}}}}, "required": ["text"]}},
            handler=lambda args: {{"text": args["text"]}},
        ))
        register.register_tools_tab(ToolsTabContribution("demo_tools", "Demo 工具", lambda parent=None: None))
        register.register_settings_panel(SettingsPanelContribution("demo_settings", "Demo 设置", lambda parent=None: None))
        register.register_chat_ui_widget(ChatUIWidgetContribution("demo_widget", lambda parent=None: None))
        register.register_prompt_patch(PromptPatchContribution("demo_patch", system_prompt_append="demo system"))
'''.strip(),
        encoding="utf-8",
    )


def _write_renderer_plugin(
    base: Path,
    plugin_id: str,
    *,
    renderer_type: str,
    priority: int = 100,
    permissions: tuple[str, ...] | None = (PERMISSION_RENDERER,),
) -> None:
    plugin_dir = _write_plugin_manifest(
        base,
        plugin_id,
        priority=priority,
        permissions=permissions,
    )
    plugin_dir.joinpath("plugin.py").write_text(
        f'''
from app.plugins import PluginBase, RendererContribution


class DemoPlugin(PluginBase):
    plugin_id = "{plugin_id}"
    plugin_version = "1.0.0"

    def initialize(self, register, context):
        register.register_renderer(RendererContribution(
            renderer_type="{renderer_type}",
            display_name="{renderer_type}",
            create=lambda create_context: None,
        ))
'''.strip(),
        encoding="utf-8",
    )


def _write_failing_plugin(
    base: Path,
    plugin_id: str,
    *,
    priority: int,
    required: bool = False,
) -> None:
    plugin_dir = _write_plugin_manifest(base, plugin_id, priority=priority, required=required)
    plugin_dir.joinpath("plugin.py").write_text(
        f'''
from app.plugins import PluginBase


class DemoPlugin(PluginBase):
    plugin_id = "{plugin_id}"

    def initialize(self, register, context):
        raise RuntimeError("boom")
'''.strip(),
        encoding="utf-8",
    )


def _write_shutdown_plugin(base: Path, plugin_id: str, *, priority: int) -> None:
    plugin_dir = _write_plugin_manifest(base, plugin_id, priority=priority)
    plugin_dir.joinpath("plugin.py").write_text(
        f'''
from app.plugins import PluginBase


class DemoPlugin(PluginBase):
    plugin_id = "{plugin_id}"

    def initialize(self, register, context):
        self.context = context

    def shutdown(self):
        path = self.context.base_dir / "shutdown_order.txt"
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(previous + "{plugin_id}\\n", encoding="utf-8")
'''.strip(),
        encoding="utf-8",
    )


def _write_event_plugin(
    base: Path,
    plugin_id: str,
    *,
    raise_hook: bool,
) -> None:
    plugin_dir = _write_plugin_manifest(
        base,
        plugin_id,
        permissions=(PERMISSION_EVENT_MESSAGE,),
    )
    raise_line = 'raise RuntimeError("hook boom")' if raise_hook else ""
    plugin_dir.joinpath("plugin.py").write_text(
        f'''
from app.plugins import PluginBase


class DemoPlugin(PluginBase):
    plugin_id = "{plugin_id}"

    def initialize(self, register, context):
        self.context = context

    def on_user_message(self, event):
        {raise_line}
        path = self.context.base_dir / f"{{self.plugin_id}}_events.txt"
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(
            previous + f"{{event.event_type}}:{{event.payload.get('text', '')}}:{{event.source}}\\n",
            encoding="utf-8",
        )
'''.strip(),
        encoding="utf-8",
    )
