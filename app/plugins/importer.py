"""Shared plugin module loader without Legacy manager dependencies."""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
from pathlib import Path
from types import ModuleType

from app.plugins.models import PluginSpec


def import_plugin_module(base_dir: Path, spec: PluginSpec, module_name: str) -> ModuleType:
    plugin_root = spec.plugin_root
    if plugin_root is None:
        raise ValueError(f"插件缺少根目录：{spec.plugin_id or spec.entry}")
    file_module = _module_file_from_relative_entry(plugin_root, module_name)
    package_module = _package_module_name(plugin_root, module_name)
    if package_module.startswith("plugins.builtin."):
        _ensure_sys_path(plugin_root.parents[2])
        return importlib.import_module(package_module)
    if file_module.is_file() and not _is_current_project_root(base_dir):
        # The embedded runtime uses a ``._pth`` file and therefore ignores
        # PYTHONPATH.  Make the trusted application root explicit before a
        # file-loaded plugin resolves its own ``plugins.<name>`` package or
        # app-local dependencies.  The user plugin directory itself is not
        # added here.
        _ensure_sys_path(base_dir)
        return _load_module_from_file(
            spec.plugin_id or plugin_root.name,
            plugin_root,
            module_name,
            file_module,
        )
    if package_module:
        _ensure_sys_path(base_dir)
        try:
            return importlib.import_module(package_module)
        except ModuleNotFoundError:
            pass
    if file_module.is_file():
        return _load_module_from_file(
            spec.plugin_id or plugin_root.name,
            plugin_root,
            module_name,
            file_module,
        )
    _ensure_sys_path(base_dir)
    return importlib.import_module(module_name)


def _package_module_name(plugin_root: Path, module_name: str) -> str:
    if (
        plugin_root.parent.name == "builtin"
        and plugin_root.parent.parent.name == "plugins"
        and (plugin_root.parent.parent / "__init__.py").is_file()
        and (plugin_root.parent / "__init__.py").is_file()
        and (plugin_root / "__init__.py").is_file()
    ):
        return f"plugins.builtin.{plugin_root.name}.{module_name}"
    if plugin_root.parent.name != "plugins":
        return ""
    if not (plugin_root.parent / "__init__.py").is_file():
        return ""
    if not (plugin_root / "__init__.py").is_file():
        return ""
    return f"plugins.{plugin_root.name}.{module_name}"


def _module_file_from_relative_entry(plugin_root: Path, module_name: str) -> Path:
    return plugin_root.joinpath(*module_name.split(".")).with_suffix(".py")


def _load_module_from_file(
    plugin_id: str,
    plugin_root: Path,
    module_name: str,
    module_path: Path,
) -> ModuleType:
    safe_plugin_id = re.sub(r"[^A-Za-z0-9_]", "_", plugin_id)
    identity = hashlib.sha256(plugin_id.encode("utf-8")).hexdigest()[:12]
    package_name = f"sakura_user_plugins.p_{safe_plugin_id}_{identity}"
    import_name = f"{package_name}.{module_name}"
    for loaded_name in tuple(sys.modules):
        if loaded_name == package_name or loaded_name.startswith(f"{package_name}."):
            del sys.modules[loaded_name]
    root_package = sys.modules.get("sakura_user_plugins")
    if root_package is None:
        root_package = ModuleType("sakura_user_plugins")
        root_package.__package__ = "sakura_user_plugins"
        root_package.__path__ = []  # type: ignore[attr-defined]
        sys.modules["sakura_user_plugins"] = root_package
    plugin_package = ModuleType(package_name)
    plugin_package.__package__ = package_name
    plugin_package.__path__ = [str(plugin_root)]  # type: ignore[attr-defined]
    sys.modules[package_name] = plugin_package
    try:
        return importlib.import_module(import_name)
    except ModuleNotFoundError as error:
        if error.name == import_name:
            raise ImportError(f"无法加载插件模块：{module_path}") from error
        raise


def _ensure_sys_path(base_dir: Path) -> None:
    path_text = str(base_dir)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)


def _is_current_project_root(base_dir: Path) -> bool:
    try:
        return base_dir.resolve() == Path.cwd().resolve()
    except OSError:
        return False


__all__ = ["import_plugin_module"]
