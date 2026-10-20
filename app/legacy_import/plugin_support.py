"""旧数据导入在独立 worker 内按需使用外置插件的兼容工具。"""
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType
import atexit
import os
import sys
from contextlib import ExitStack

from app.plugins.dependency_imports import dependency_import_paths, dependency_dll_handles

from app.storage.runtime_roots import RuntimeRoots

_roots: RuntimeRoots | None = None
_dependency_dlls = ExitStack()
atexit.register(_dependency_dlls.close)


def configure_plugin_support(roots: RuntimeRoots) -> None:
    global _roots
    _roots = roots


def migration_module(name: str) -> ModuleType:
    if _roots is None:
        # Repository tests call importer functions directly, outside the CLI worker.
        return import_module(f"plugins.optional.{name}")
    from app.plugins.inventory import PluginInventory
    from app.plugins.dependencies import PluginDependencyRoots
    from app.storage.paths import StoragePaths

    directory, _, module_name = name.partition(".")
    plugin_id = {
        "sakura_mem0": "sakura.memory.mem0",
        "sakura_gpt_sovits": "sakura.tts.gpt-sovits",
        "sakura_genie": "sakura.tts.genie",
    }[directory]
    record = next((r for r in PluginInventory(_roots).scan().records
                   if r.source == "user" and r.plugin_id == plugin_id), None)
    if record is None:
        raise ValueError(f"导入这部分旧数据需要插件 {plugin_id}，请先从插件市场安装后重试。")
    plugin_root = StoragePaths(_roots.user_root).user_plugins_dir / record.directory_name
    dependencies = PluginDependencyRoots(_roots.user_root).verified_root(plugin_id, plugin_root)
    package_name = f"sakura_legacy_{directory}"
    if package_name not in sys.modules:
        if dependencies is not None:
            for path in dependency_import_paths(dependencies, windows=os.name == "nt"):
                if str(path) not in sys.path:
                    sys.path.append(str(path))
            for handle in dependency_dll_handles(dependencies):
                _dependency_dlls.enter_context(handle)
        spec = spec_from_file_location(package_name, plugin_root / "__init__.py", submodule_search_locations=[str(plugin_root)])
        package = module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return import_module(f"{package_name}.{module_name}")
