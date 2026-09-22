"""旧数据导入在独立 worker 内按需使用外置插件的兼容工具。"""
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
import sys

from app.storage.runtime_roots import RuntimeRoots

_roots: RuntimeRoots | None = None


def configure_plugin_support(roots: RuntimeRoots) -> None:
    global _roots
    _roots = roots


def migration_module(name: str) -> ModuleType:
    if _roots is None:
        # Repository tests call importer functions directly, outside the CLI worker.
        return import_module(f"plugins.optional.{name}")
    from app.plugins.bundled_migrations import MIGRATIONS, ensure_external_plugin
    from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
    from app.plugins.dependencies import PluginDependencyRoots
    from app.storage.paths import StoragePaths

    directory, _, module_name = name.partition(".")
    plugin_id = next(key for key, value in MIGRATIONS.items() if value == directory)
    desired = PluginDesiredStateStore(_roots.user_root).read()
    ensure_external_plugin(_roots, plugin_id, enabled=desired.get(plugin_id, False))
    record = next(r for r in PluginInventory(_roots).scan().records if r.source == "user" and r.plugin_id == plugin_id)
    plugin_root = StoragePaths(_roots.user_root).user_plugins_dir / record.directory_name
    dependencies = PluginDependencyRoots(_roots.user_root).verified_root(plugin_id, plugin_root)
    if dependencies is not None and str(dependencies) not in sys.path:
        sys.path.append(str(dependencies))
    package_name = f"sakura_legacy_{directory}"
    if package_name not in sys.modules:
        spec = spec_from_file_location(package_name, plugin_root / "__init__.py", submodule_search_locations=[str(plugin_root)])
        package = module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return import_module(f"{package_name}.{module_name}")
