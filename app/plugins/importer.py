"""Shared plugin module loader without Legacy manager dependencies."""

from __future__ import annotations

import importlib
import importlib.util
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


__all__ = ["import_plugin_module"]
