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
from app.plugins.base import PluginBase, PluginContext
from app.plugins.capabilities import PluginCapabilities, PluginCapabilityRegistry
from app.plugins.discovery import PluginDiscovery
from app.plugins.models import (
    KNOWN_PLUGIN_PERMISSIONS,
    PERMISSION_CHAT_UI,
    PERMISSION_EVENT_APP,
    PERMISSION_EVENT_CHARACTER,
    PERMISSION_EVENT_MESSAGE,
    PERMISSION_EVENT_TTS,
    PERMISSION_PROMPT_PATCH,
    PERMISSION_SETTINGS_PANEL,
    PERMISSION_TOOL,
    PERMISSION_TOOLS_TAB,
    PLUGIN_API_VERSION,
    PluginEvent,
    PluginManifest,
    PluginManifestView,
    PluginSpec,
    ToolContribution,
)
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

_LEGACY_SDK_DEFAULT_PERMISSIONS = (
    PERMISSION_TOOL,
    PERMISSION_TOOLS_TAB,
    PERMISSION_SETTINGS_PANEL,
    PERMISSION_CHAT_UI,
    PERMISSION_PROMPT_PATCH,
)


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
    _loaded: list[PluginLoadResult] = field(default_factory=list)
    _plugins: list[PluginBase] = field(default_factory=list)
    _active_plugins: list[tuple[PluginBase, PluginManifest]] = field(default_factory=list)

    def load_from_config(self, tool_registry: ToolRegistry) -> None:
        """加载配置中的启用插件并注册工具。"""
        self.load_all(tool_registry)

    def load_all(self, tool_registry: ToolRegistry | None = None) -> list[PluginLoadResult]:
        """加载所有启用插件；传入 ToolRegistry 时同步注册工具贡献。"""
        specs = PluginDiscovery(self.base_dir).discover_enabled()
        results: list[PluginLoadResult] = []
        known_tool_names = _tool_names_from_registry(tool_registry)
        self._plugins = []
        self._active_plugins = []
        for spec in specs:
            result = self._load_one(spec, tool_registry, known_tool_names)
            results.append(result)
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
    ) -> PluginLoadResult:
        result = PluginLoadResult(spec=spec)
        plugin: PluginBase | None = None
        try:
            _clear_legacy_registered_tools()
            plugin = _import_plugin(self.base_dir, spec)
            manifest = _build_manifest(plugin, spec)
            if not manifest.permissions and _is_legacy_sdk_plugin(plugin):
                manifest = replace(manifest, permissions=_LEGACY_SDK_DEFAULT_PERMISSIONS)
                debug_log(
                    "PluginManager",
                    "旧 SDK 插件缺少权限声明，已按兼容权限加载",
                    {"entry": spec.entry, "plugin_id": manifest.plugin_id},
                )
            _validate_manifest(manifest)
            result.manifest = manifest

            capability_registry = PluginCapabilityRegistry()
            context = _build_plugin_context(self.base_dir, manifest)
            _initialize_plugin(plugin, capability_registry, context)
            legacy_tool_contributions = _consume_legacy_registered_tool_contributions()
            all_tool_contributions = [
                *capability_registry.tools,
                *legacy_tool_contributions,
            ]

            _validate_capability_permissions(
                capability_registry,
                manifest.permissions,
                extra_tools=legacy_tool_contributions,
            )
            _validate_tool_contributions(all_tool_contributions, known_tool_names)

            capabilities = PluginCapabilities(
                plugin_id=manifest.plugin_id,
                tools=list(all_tool_contributions),
                settings_panels=list(capability_registry.settings_panels),
                tools_tabs=list(capability_registry.tools_tabs),
                chat_ui_widgets=list(capability_registry.chat_ui_widgets),
                prompt_patches=list(capability_registry.prompt_patches),
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
                },
            )
        except Exception as exc:
            result.error = str(exc)
            if plugin is not None:
                _shutdown_quietly(plugin)
            debug_log(
                "PluginManager",
                "插件加载失败",
                {"entry": spec.entry, "plugin_id": spec.plugin_id, "error": str(exc)},
            )
        finally:
            _clear_legacy_registered_tools()
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

    def shutdown_all(self) -> None:
        """逆序关闭所有已加载插件。"""
        for plugin in reversed(self._plugins):
            _shutdown_quietly(plugin)

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
    if manifest.api_version != PLUGIN_API_VERSION:
        raise ValueError(
            f"插件 API 版本不支持：{manifest.api_version}（当前支持 {PLUGIN_API_VERSION}）"
        )
    if not manifest.permissions:
        raise ValueError("插件缺少 permissions 声明")
    unknown = sorted(set(manifest.permissions) - KNOWN_PLUGIN_PERMISSIONS)
    if unknown:
        raise ValueError(f"插件声明了未知权限：{', '.join(unknown)}")


def _validate_capability_permissions(
    registry: PluginCapabilityRegistry,
    permissions: tuple[str, ...],
    *,
    extra_tools: list[ToolContribution] | None = None,
) -> None:
    permission_set = set(permissions)
    checks = (
        ([*registry.tools, *(extra_tools or [])], PERMISSION_TOOL, "工具"),
        (registry.tools_tabs, PERMISSION_TOOLS_TAB, "工具页"),
        (registry.settings_panels, PERMISSION_SETTINGS_PANEL, "设置面板"),
        (registry.chat_ui_widgets, PERMISSION_CHAT_UI, "聊天 UI"),
        (registry.prompt_patches, PERMISSION_PROMPT_PATCH, "提示词补丁"),
    )
    for contributions, permission, label in checks:
        if contributions and permission not in permission_set:
            raise ValueError(f"插件贡献了{label}，但未声明权限 {permission}")


def _string_attr(plugin: PluginBase, name: str) -> str:
    value = getattr(plugin, name, "")
    if isinstance(value, str):
        return value.strip()
    return ""


def _build_plugin_context(base_dir: Path, manifest: PluginManifest) -> PluginContext:
    plugin_root = manifest.plugin_root or base_dir / "plugins" / manifest.plugin_id
    data_dir = StoragePaths(base_dir).plugin_data_for(manifest.plugin_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_view = PluginManifestView(
        plugin_id=manifest.plugin_id,
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        api_version=manifest.api_version,
        priority=manifest.priority,
        enabled=manifest.enabled,
        required=manifest.required,
        permissions=manifest.permissions,
    )
    return PluginContext(
        base_dir=base_dir,
        plugin_root=plugin_root,
        data_dir=data_dir,
        manifest=manifest_view,
    )


def _initialize_plugin(
    plugin: PluginBase,
    register: PluginCapabilityRegistry,
    context: PluginContext,
) -> None:
    """初始化插件，同时兼容旧 SDK 的三参数 initialize。"""

    initialize = plugin.initialize
    try:
        parameters = list(inspect.signature(initialize).parameters.values())
    except (TypeError, ValueError):
        initialize(register, context)
        return
    has_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters)
    if has_varargs or len(parameters) >= 3:
        from sdk.plugin_host_context import PluginHostContext

        initialize(  # type: ignore[misc]
            register,
            context.plugin_root,
            PluginHostContext(base_dir=context.base_dir),
        )
        return
    initialize(register, context)


def _is_legacy_sdk_plugin(plugin: PluginBase) -> bool:
    try:
        from sdk.plugin import PluginBase as SDKPluginBase
    except Exception:
        return False
    return isinstance(plugin, SDKPluginBase)


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


def _clear_legacy_registered_tools() -> None:
    module = sys.modules.get("sdk.tool_registry")
    clear = getattr(module, "clear_registered_tools", None) if module is not None else None
    if callable(clear):
        clear()


def _consume_legacy_registered_tool_contributions() -> list[ToolContribution]:
    module = sys.modules.get("sdk.tool_registry")
    registered_tools = getattr(module, "registered_tools", None) if module is not None else None
    if not callable(registered_tools):
        return []
    contributions: list[ToolContribution] = []
    for registered in registered_tools():
        parameters = getattr(registered, "parameters", {})
        if not isinstance(parameters, dict):
            parameters = {}
        contributions.append(
            ToolContribution(
                name=str(getattr(registered, "name", "")),
                description=str(getattr(registered, "description", "")),
                parameters=parameters,
                handler=_legacy_tool_handler(getattr(registered, "func", None), parameters),
                group=str(getattr(registered, "group", "default")),
                risk=str(getattr(registered, "risk", "low")),
                requires_confirmation=bool(getattr(registered, "requires_confirmation", False)),
            )
        )
    _clear_legacy_registered_tools()
    return contributions


def _legacy_tool_handler(func: Any, parameters: dict[str, Any]) -> Any:
    if not callable(func):
        return None

    def handler(arguments: dict[str, Any]) -> Any:
        properties = parameters.get("properties")
        if not isinstance(properties, dict):
            return func(**arguments)
        kwargs = {
            name: arguments[name]
            for name in properties
            if name in arguments
        }
        return func(**kwargs)

    return handler


def _shutdown_quietly(plugin: PluginBase) -> None:
    try:
        plugin.shutdown()
    except Exception as exc:
        debug_log(
            "PluginManager",
            "插件关闭失败",
            {"plugin": getattr(plugin, "plugin_id", "unknown"), "error": str(exc)},
        )
