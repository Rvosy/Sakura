"""app/plugins/manager.py — Sakura 原生插件管理器。"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from app.agent.tools.registry import Tool
from app.agent.tools import ToolRegistry
from app.core.debug_log import debug_log
from app.core.resource_manager import DEFAULT_THREAD_SHUTDOWN_WAIT_MS, ResourceRegistry
from app.plugins.base import PluginBase, PluginContext
from app.plugins.capabilities import PluginCapabilities, PluginCapabilityRegistry
from app.plugins.discovery import PluginDiscovery
from app.plugins.events import PluginEventBus, ScopedEventBus
from app.plugins.models import (
    KNOWN_PLUGIN_PERMISSIONS,
    PERMISSION_CHAT_UI,
    PERMISSION_CONTEXT_PROVIDER,
    PERMISSION_EVENT_APP,
    PERMISSION_EVENT_CHARACTER,
    PERMISSION_EVENT_MESSAGE,
    PERMISSION_EVENT_TTS,
    PERMISSION_PROMPT_PATCH,
    PERMISSION_RENDERER,
    PERMISSION_SETTINGS_PANEL,
    PERMISSION_TOOL,
    PERMISSION_TOOLS_TAB,
    SUPPORTED_API_VERSIONS,
    ContextProviderContribution,
    PluginEvent,
    PluginManifest,
    PluginManifestView,
    PluginSpec,
    RendererContribution,
    ToolContribution,
)
from app.plugins.services import PluginServices
from app.storage.paths import StoragePaths


OPENAI_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PLUGIN_EVENT_APP_START = "app.start"
PLUGIN_EVENT_USER_MESSAGE = "message.user"
PLUGIN_EVENT_AI_MESSAGE = "message.ai"
PLUGIN_EVENT_TTS_START = "tts.start"
PLUGIN_EVENT_TTS_END = "tts.end"
PLUGIN_EVENT_CHARACTER_LOADED = "character.loaded"

_EVENT_HOOKS: dict[str, tuple[str, str]] = {
    PLUGIN_EVENT_APP_START: ("on_app_start", PERMISSION_EVENT_APP),
    PLUGIN_EVENT_USER_MESSAGE: ("on_user_message", PERMISSION_EVENT_MESSAGE),
    PLUGIN_EVENT_AI_MESSAGE: ("on_ai_message", PERMISSION_EVENT_MESSAGE),
    PLUGIN_EVENT_TTS_START: ("on_tts_start", PERMISSION_EVENT_TTS),
    PLUGIN_EVENT_TTS_END: ("on_tts_end", PERMISSION_EVENT_TTS),
    PLUGIN_EVENT_CHARACTER_LOADED: ("on_character_loaded", PERMISSION_EVENT_CHARACTER),
}

@dataclass
class PluginLoadResult:
    """单个插件的加载结果。"""

    spec: PluginSpec
    manifest: PluginManifest | None = None
    capabilities: PluginCapabilities | None = None
    error: str | None = None
    loaded: bool = False


@dataclass
class PluginManager:
    """发现、加载、校验并收集 Sakura 插件贡献。"""

    base_dir: Path
    resource_registry: ResourceRegistry | None = None
    _loaded: list[PluginLoadResult] = field(default_factory=list)
    _plugins: list[PluginBase] = field(default_factory=list)
    _active_plugins: list[tuple[PluginBase, PluginManifest]] = field(default_factory=list)
    _event_bus: PluginEventBus = field(default_factory=PluginEventBus)
    _services: PluginServices = field(default_factory=PluginServices)

    def __post_init__(self) -> None:
        self.resource_registry = self.resource_registry or ResourceRegistry()
        self._services.set_resource_registry(self.resource_registry)

    @property
    def event_bus(self) -> PluginEventBus:
        """宿主用于 emit 的事件总线。"""
        return self._event_bus

    @property
    def services(self) -> PluginServices:
        """宿主用于注入真实后端的服务门面。"""
        return self._services

    def emit_bus_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """向事件总线派发事件（供宿主在关键时机调用）。"""
        self._event_bus.emit(event_name, payload)

    def load_from_config(self, tool_registry: ToolRegistry) -> None:
        """加载配置中的启用插件并注册工具。"""
        self.load_all(tool_registry)

    def load_all(self, tool_registry: ToolRegistry | None = None) -> list[PluginLoadResult]:
        """加载所有启用插件；传入 ToolRegistry 时同步注册工具贡献。"""
        specs = PluginDiscovery(self.base_dir).discover_enabled()
        results: list[PluginLoadResult] = []
        known_tool_names = _tool_names_from_registry(tool_registry)
        known_renderer_types: set[str] = set()
        self._plugins = []
        self._active_plugins = []
        for spec in specs:
            result = self._load_one(spec, tool_registry, known_tool_names, known_renderer_types)
            results.append(result)
            if result.loaded and result.capabilities is not None:
                known_renderer_types.update(
                    _normalize_renderer_type(renderer.renderer_type)
                    for renderer in result.capabilities.renderers
                )
            if result.error and spec.required:
                debug_log(
                    "PluginManager",
                    "必需插件加载失败，中止",
                    {"entry": spec.entry, "plugin_id": spec.plugin_id, "error": result.error},
                )
                break
        self._loaded = results
        return results

    def _load_one(
        self,
        spec: PluginSpec,
        tool_registry: ToolRegistry | None,
        known_tool_names: set[str],
        known_renderer_types: set[str],
    ) -> PluginLoadResult:
        result = PluginLoadResult(spec=spec)
        plugin: PluginBase | None = None
        try:
            plugin = _import_plugin(self.base_dir, spec)
            manifest = _build_manifest(plugin, spec)
            _validate_manifest(manifest)
            result.manifest = manifest

            capability_registry = PluginCapabilityRegistry()
            context = _build_plugin_context(
                self.base_dir,
                manifest,
                event_bus=self._event_bus,
                services=self._services.for_plugin(
                    manifest.plugin_id,
                    manifest.permissions,
                ),
            )
            _initialize_plugin(plugin, capability_registry, context)
            all_tool_contributions = list(capability_registry.tools)

            _validate_capability_permissions(
                capability_registry,
                manifest.permissions,
            )
            _validate_tool_contributions(all_tool_contributions, known_tool_names)
            _validate_renderer_contributions(capability_registry.renderers, known_renderer_types)

            capabilities = PluginCapabilities(
                plugin_id=manifest.plugin_id,
                tools=list(all_tool_contributions),
                settings_panels=[
                    replace(panel, plugin_id=manifest.plugin_id)
                    for panel in capability_registry.settings_panels
                ],
                tools_tabs=list(capability_registry.tools_tabs),
                chat_ui_widgets=list(capability_registry.chat_ui_widgets),
                prompt_patches=list(capability_registry.prompt_patches),
                context_providers=list(capability_registry.context_providers),
                renderers=[
                    replace(renderer, plugin_id=manifest.plugin_id)
                    for renderer in capability_registry.renderers
                ],
            )
            if tool_registry is not None:
                for contribution in capabilities.tools:
                    tool_registry.register(_contribution_to_app_tool(contribution))
                    known_tool_names.add(contribution.name)
            else:
                known_tool_names.update(contribution.name for contribution in capabilities.tools)
            result.capabilities = capabilities
            result.loaded = True
            self._plugins.append(plugin)
            self._active_plugins.append((plugin, manifest))
            debug_log(
                "PluginManager",
                "插件已加载",
                {
                    "plugin_id": manifest.plugin_id,
                    "tools": len(capabilities.tools),
                    "tools_tabs": len(capabilities.tools_tabs),
                    "settings_panels": len(capabilities.settings_panels),
                    "chat_ui_widgets": len(capabilities.chat_ui_widgets),
                    "prompt_patches": len(capabilities.prompt_patches),
                    "context_providers": len(capabilities.context_providers),
                    "renderers": len(capabilities.renderers),
                },
            )
        except Exception as exc:
            result.error = str(exc)
            if plugin is not None:
                _shutdown_quietly(plugin)
            # 加载失败时清理可能已注册的事件订阅，避免残留 handler。
            if result.manifest is not None:
                self._services.resources.stop_plugin(result.manifest.plugin_id)
                self._event_bus.remove_plugin(result.manifest.plugin_id)
            debug_log(
                "PluginManager",
                "插件加载失败",
                {"entry": spec.entry, "plugin_id": spec.plugin_id, "error": str(exc)},
            )
        return result

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "host",
    ) -> None:
        """向拥有对应权限的插件派发生命周期事件。"""
        hook = _EVENT_HOOKS.get(event_type)
        if hook is None:
            debug_log("PluginManager", "忽略未知插件事件", {"event_type": event_type})
            return
        hook_name, permission = hook
        event = PluginEvent(event_type=event_type, payload=payload or {}, source=source)
        for plugin, manifest in list(self._active_plugins):
            if permission not in manifest.permissions:
                continue
            callback = getattr(plugin, hook_name, None)
            if not callable(callback):
                continue
            try:
                callback(event)
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "PluginManager",
                    "插件事件 hook 失败",
                    {
                        "plugin_id": manifest.plugin_id,
                        "event_type": event_type,
                        "error": str(exc),
                    },
                )

    def collect_tools(self) -> list[ToolContribution]:
        tools: list[ToolContribution] = []
        for result in self._loaded:
            if result.capabilities:
                tools.extend(result.capabilities.tools)
        return tools

    def collect_settings_panels(self) -> list:
        panels: list = []
        for result in self._loaded:
            if result.capabilities:
                panels.extend(result.capabilities.settings_panels)
        return panels

    def collect_tools_tabs(self) -> list:
        tabs: list = []
        for result in self._loaded:
            if result.capabilities:
                tabs.extend(result.capabilities.tools_tabs)
        return tabs

    def collect_chat_ui_widgets(self) -> list:
        widgets: list = []
        for result in self._loaded:
            if result.capabilities:
                widgets.extend(result.capabilities.chat_ui_widgets)
        return widgets

    def collect_prompt_patches(self) -> list:
        patches: list = []
        for result in self._loaded:
            if result.capabilities:
                patches.extend(result.capabilities.prompt_patches)
        return patches

    def collect_context_providers(self) -> list[ContextProviderContribution]:
        providers: list[ContextProviderContribution] = []
        for result in self._loaded:
            if result.capabilities:
                providers.extend(result.capabilities.context_providers)
        return providers

    def collect_renderers(self) -> list[RendererContribution]:
        renderers: list[RendererContribution] = []
        for result in self._loaded:
            if result.capabilities:
                renderers.extend(result.capabilities.renderers)
        return renderers

    @property
    def tools_tabs(self) -> list:
        return self.collect_tools_tabs()

    @property
    def settings_panels(self) -> list:
        return self.collect_settings_panels()

    @property
    def chat_ui_widgets(self) -> list:
        return self.collect_chat_ui_widgets()

    @property
    def prompt_patches(self) -> list:
        return self.collect_prompt_patches()

    @property
    def context_providers(self) -> list[ContextProviderContribution]:
        return self.collect_context_providers()

    @property
    def renderers(self) -> list[RendererContribution]:
        return self.collect_renderers()

    def shutdown_all(self) -> None:
        """逆序关闭所有已加载插件，并清理其事件订阅；可重复调用。"""
        active_plugins = list(reversed(self._active_plugins))
        self._active_plugins = []
        for plugin, manifest in active_plugins:
            self._services.resources.stop_plugin(
                manifest.plugin_id,
                DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
            )
            _shutdown_quietly(plugin)
            self._event_bus.remove_plugin(manifest.plugin_id)

    @property
    def loaded_count(self) -> int:
        return sum(1 for result in self._loaded if result.loaded)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self._loaded if result.error)

    @property
    def results(self) -> list[PluginLoadResult]:
        return list(self._loaded)


def _tool_names_from_registry(tool_registry: ToolRegistry | None) -> set[str]:
    if tool_registry is None:
        return set()
    return {tool.name for tool in tool_registry.all()}


def _import_plugin(base_dir: Path, spec: PluginSpec) -> PluginBase:
    module_name, _, class_name = spec.entry.partition(":")
    if not module_name or not class_name:
        raise ValueError(f"插件入口格式无效：{spec.entry}")
    module = _import_plugin_module(base_dir, spec, module_name)
    plugin_cls = getattr(module, class_name)
    if not isinstance(plugin_cls, type):
        raise TypeError(f"插件入口不是类：{spec.entry}")
    plugin = plugin_cls()
    if not isinstance(plugin, PluginBase):
        raise TypeError(f"插件入口不是 PluginBase：{spec.entry}")
    return plugin


def _import_plugin_module(base_dir: Path, spec: PluginSpec, module_name: str) -> ModuleType:
    plugin_root = spec.plugin_root
    if plugin_root is None:
        raise ValueError(f"插件缺少根目录：{spec.plugin_id or spec.entry}")
    file_module = _module_file_from_relative_entry(plugin_root, module_name)
    if file_module.is_file() and not _is_current_project_root(base_dir):
        return _load_module_from_file(spec.plugin_id or plugin_root.name, module_name, file_module)
    package_module = _package_module_name(plugin_root, module_name)
    if package_module:
        _ensure_sys_path(base_dir)
        try:
            return importlib.import_module(package_module)
        except ModuleNotFoundError:
            pass
    if file_module.is_file():
        return _load_module_from_file(spec.plugin_id or plugin_root.name, module_name, file_module)
    _ensure_sys_path(base_dir)
    return importlib.import_module(module_name)


def _package_module_name(plugin_root: Path, module_name: str) -> str:
    if plugin_root.parent.name != "plugins":
        return ""
    if not (plugin_root.parent / "__init__.py").is_file():
        return ""
    if not (plugin_root / "__init__.py").is_file():
        return ""
    return f"plugins.{plugin_root.name}.{module_name}"


def _module_file_from_relative_entry(plugin_root: Path, module_name: str) -> Path:
    return plugin_root.joinpath(*module_name.split(".")).with_suffix(".py")


def _load_module_from_file(plugin_id: str, module_name: str, module_path: Path) -> ModuleType:
    safe_plugin_id = re.sub(r"[^A-Za-z0-9_]", "_", plugin_id)
    safe_module_name = re.sub(r"[^A-Za-z0-9_]", "_", module_name)
    import_name = f"sakura_user_plugins.{safe_plugin_id}.{safe_module_name}"
    spec = importlib.util.spec_from_file_location(import_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_sys_path(base_dir: Path) -> None:
    path_text = str(base_dir)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)


def _is_current_project_root(base_dir: Path) -> bool:
    try:
        return base_dir.resolve() == Path.cwd().resolve()
    except OSError:
        return False


def _build_manifest(plugin: PluginBase, spec: PluginSpec) -> PluginManifest:
    plugin_id = _string_attr(plugin, "plugin_id") or spec.plugin_id
    if not plugin_id:
        raise ValueError(f"插件缺少 plugin_id：{spec.entry}")
    version = _string_attr(plugin, "plugin_version") or spec.version
    return PluginManifest(
        plugin_id=plugin_id,
        name=spec.name or plugin_id,
        author=spec.author,
        description=spec.description,
        version=version or "0.0.0",
        api_version=spec.api_version,
        priority=spec.priority,
        enabled=spec.enabled,
        required=spec.required,
        entry=spec.entry,
        permissions=spec.permissions,
        plugin_root=spec.plugin_root,
    )


def _validate_manifest(manifest: PluginManifest) -> None:
    if manifest.api_version not in SUPPORTED_API_VERSIONS:
        supported = ", ".join(str(version) for version in sorted(SUPPORTED_API_VERSIONS))
        raise ValueError(
            f"插件 API 版本不支持：{manifest.api_version}（当前支持 {supported}）"
        )
    if not manifest.permissions:
        raise ValueError("插件缺少 permissions 声明")
    unknown = sorted(set(manifest.permissions) - KNOWN_PLUGIN_PERMISSIONS)
    if unknown:
        raise ValueError(f"插件声明了未知权限：{', '.join(unknown)}")


def _validate_capability_permissions(
    registry: PluginCapabilityRegistry,
    permissions: tuple[str, ...],
) -> None:
    permission_set = set(permissions)
    checks = (
        (registry.tools, PERMISSION_TOOL, "工具"),
        (registry.tools_tabs, PERMISSION_TOOLS_TAB, "工具页"),
        (registry.settings_panels, PERMISSION_SETTINGS_PANEL, "设置面板"),
        (registry.chat_ui_widgets, PERMISSION_CHAT_UI, "聊天 UI"),
        (registry.prompt_patches, PERMISSION_PROMPT_PATCH, "提示词补丁"),
        (registry.context_providers, PERMISSION_CONTEXT_PROVIDER, "上下文提供者"),
        (registry.renderers, PERMISSION_RENDERER, "角色渲染器"),
    )
    for contributions, permission, label in checks:
        if contributions and permission not in permission_set:
            raise ValueError(f"插件贡献了{label}，但未声明权限 {permission}")


def _string_attr(plugin: PluginBase, name: str) -> str:
    value = getattr(plugin, name, "")
    if isinstance(value, str):
        return value.strip()
    return ""


def _build_plugin_context(
    base_dir: Path,
    manifest: PluginManifest,
    *,
    event_bus: PluginEventBus | None = None,
    services: Any = None,
) -> PluginContext:
    plugin_root = manifest.plugin_root or base_dir / "plugins" / manifest.plugin_id
    data_dir = StoragePaths(base_dir).plugin_data_for(manifest.plugin_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_view = PluginManifestView(
        plugin_id=manifest.plugin_id,
        name=manifest.name,
        author=manifest.author,
        description=manifest.description,
        version=manifest.version,
        api_version=manifest.api_version,
        priority=manifest.priority,
        enabled=manifest.enabled,
        required=manifest.required,
        permissions=manifest.permissions,
    )
    # 每插件一个 ScopedEventBus（只开放订阅）；services 为共享门面。
    scoped_events = (
        ScopedEventBus(event_bus, manifest.plugin_id) if event_bus is not None else None
    )
    return PluginContext(
        base_dir=base_dir,
        plugin_root=plugin_root,
        data_dir=data_dir,
        manifest=manifest_view,
        events=scoped_events,
        services=services,
    )


def _initialize_plugin(
    plugin: PluginBase,
    register: PluginCapabilityRegistry,
    context: PluginContext,
) -> None:
    """初始化插件。"""
    plugin.initialize(register, context)


def _validate_tool_contributions(
    tools: list[ToolContribution],
    known_tool_names: set[str],
) -> None:
    local_tool_names: set[str] = set()
    for contribution in tools:
        if not callable(contribution.handler):
            raise ValueError(f"插件工具缺少处理器：{contribution.name}")
        if not OPENAI_TOOL_NAME_RE.fullmatch(contribution.name):
            raise ValueError(f"插件工具名无效：{contribution.name}")
        if contribution.name in known_tool_names or contribution.name in local_tool_names:
            raise ValueError(f"插件工具名重复：{contribution.name}")
        local_tool_names.add(contribution.name)


def _normalize_renderer_type(renderer_type: str) -> str:
    return str(renderer_type or "").strip().lower()


def _validate_renderer_contributions(
    renderers: list[RendererContribution],
    known_renderer_types: set[str],
) -> None:
    local_types: set[str] = set()
    for contribution in renderers:
        renderer_type = _normalize_renderer_type(contribution.renderer_type)
        if not renderer_type:
            raise ValueError("插件渲染器缺少 renderer_type")
        if renderer_type == "default":
            raise ValueError("插件不能注册保留渲染器类型：default")
        if not callable(contribution.create):
            raise ValueError(f"插件渲染器缺少创建函数：{renderer_type}")
        if renderer_type in known_renderer_types or renderer_type in local_types:
            raise ValueError(f"插件渲染器类型重复：{renderer_type}")
        local_types.add(renderer_type)


def _contribution_to_app_tool(contribution: ToolContribution) -> Tool:
    return Tool(
        name=contribution.name,
        description=contribution.description,
        parameters=contribution.parameters,
        handler=_normalize_tool_handler(contribution.handler),
        requires_confirmation=contribution.requires_confirmation,
        group=contribution.group,
        risk=contribution.risk,
        capability=contribution.capability,
        source="plugin",
    )


def _normalize_tool_handler(handler: Any) -> Any:
    """兼容 handler(args) 与 handler(**kwargs) 两种插件写法。"""

    if handler is None or not callable(handler):
        return None
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return lambda arguments: handler(arguments)
    if not parameters:
        return lambda _arguments: handler()
    if len(parameters) == 1:
        parameter = parameters[0]
        annotation = parameter.annotation
        if (
            parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
            and (
                parameter.name in {"args", "arguments"}
                or annotation in {dict, dict[str, Any]}
            )
        ):
            return lambda arguments: handler(arguments)

    def wrapped(arguments: dict[str, Any]) -> Any:
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return handler(**arguments)
        kwargs = {
            parameter.name: arguments[parameter.name]
            for parameter in parameters
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
            and parameter.name in arguments
        }
        return handler(**kwargs)

    return wrapped


def _shutdown_quietly(plugin: PluginBase) -> None:
    try:
        plugin.shutdown()
    except Exception as exc:
        debug_log(
            "PluginManager",
            "插件关闭失败",
            {"plugin": getattr(plugin, "plugin_id", "unknown"), "error": str(exc)},
        )
