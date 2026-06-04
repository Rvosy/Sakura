"""app/plugins/discovery.py — 插件发现。

负责扫描 plugins/ 目录和 plugins.yaml 配置，
发现可用插件并解析其清单信息。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from app.core.debug_log import debug_log
from app.plugins.models import PluginSpec


class PluginDiscovery:
    """从配置文件和插件目录发现可用插件。

    职责：
    - 解析 data/config/plugins.yaml 中的插件入口
    - 按 priority 排序
    - 检查 enabled 状态
    """

    def __init__(self, base_dir: Path, config_path: Path | None = None) -> None:
        self.base_dir = base_dir
        self._config_path = config_path or base_dir / "data" / "config" / "plugins.yaml"

    def discover(self) -> list[PluginSpec]:
        """发现所有已配置的插件（按优先级降序排列）。"""
        specs = self._load_specs()
        specs.sort(key=lambda s: s.priority, reverse=True)
        return specs

    def discover_enabled(self) -> list[PluginSpec]:
        """发现所有启用的插件。"""
        return [s for s in self.discover() if s.enabled]

    def _load_specs(self) -> list[PluginSpec]:
        manifest_specs = self._load_manifest_specs()
        overrides, legacy_specs = self._load_config_specs()
        specs: list[PluginSpec] = []
        seen_ids: set[str] = set()
        for spec in manifest_specs:
            override = overrides.get(spec.plugin_id)
            if override:
                spec = replace(
                    spec,
                    enabled=override.enabled,
                    priority=override.priority if override.priority_override else spec.priority,
                    required=override.required or spec.required,
                )
            specs.append(spec)
            if spec.plugin_id:
                seen_ids.add(spec.plugin_id)
        for spec in legacy_specs:
            if spec.plugin_id and spec.plugin_id in seen_ids:
                continue
            specs.append(spec)
        return specs

    def _load_manifest_specs(self) -> list[PluginSpec]:
        plugins_dir = self.base_dir / "plugins"
        if not plugins_dir.is_dir():
            return []
        specs: list[PluginSpec] = []
        for manifest_path in sorted(plugins_dir.glob("*/plugin.yaml")):
            raw = _load_yaml(manifest_path)
            if not isinstance(raw, dict):
                if raw is not None:
                    debug_log(
                        "PluginDiscovery",
                        "插件清单格式无效，已跳过",
                        {"path": manifest_path, "type": type(raw).__name__},
                    )
                continue
            spec = _spec_from_manifest(raw, manifest_path.parent)
            if spec is not None:
                specs.append(spec)
            else:
                debug_log(
                    "PluginDiscovery",
                    "插件清单缺少必要字段，已跳过",
                    {"path": manifest_path},
                )
        return specs

    def _load_config_specs(self) -> tuple[dict[str, PluginSpec], list[PluginSpec]]:
        raw = _load_yaml(self._config_path)
        if not isinstance(raw, list):
            if raw is not None:
                debug_log(
                    "PluginDiscovery",
                    "插件配置格式无效，已忽略",
                    {"path": self._config_path, "type": type(raw).__name__},
                )
            return {}, []
        overrides: dict[str, PluginSpec] = {}
        legacy_specs: list[PluginSpec] = []
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            plugin_id = _string_value(item.get("id"))
            entry = item.get("entry")
            priority = _int_value(item.get("priority"), 100 - idx)
            priority_override = "priority" in item
            enabled = _bool_value(item.get("enabled"), True)
            required = _bool_value(item.get("required"), False)
            if plugin_id:
                overrides[plugin_id] = PluginSpec(
                    entry=_string_value(entry) or "",
                    plugin_id=plugin_id,
                    enabled=enabled,
                    priority=priority,
                    required=required,
                    description=_string_value(item.get("description")),
                    source="config",
                    priority_override=priority_override,
                )
                continue
            if not isinstance(entry, str) or not entry.strip():
                continue
            legacy_specs.append(
                PluginSpec(
                    entry=entry.strip(),
                    plugin_id=_plugin_id_from_entry(entry.strip()),
                    enabled=enabled,
                    priority=priority,
                    required=required,
                    description=_string_value(item.get("description")),
                    source="config",
                )
            )
        return overrides, legacy_specs


def load_plugin_specs(path: Path) -> list[PluginSpec]:
    """兼容旧调用：从 plugins.yaml 所在项目推导 base_dir 后发现插件。"""
    config_path = path
    if path.name == "plugins.yaml" and path.parent.name == "config" and path.parent.parent.name == "data":
        base_dir = path.parent.parent.parent
    else:
        base_dir = path.parent
    return PluginDiscovery(base_dir, config_path=config_path).discover()


def _spec_from_manifest(raw: dict[str, Any], plugin_root: Path) -> PluginSpec | None:
    plugin_id = _string_value(raw.get("id") or raw.get("plugin_id"))
    entry = _string_value(raw.get("entry"))
    if not plugin_id or not entry:
        return None
    return PluginSpec(
        entry=entry,
        plugin_id=plugin_id,
        name=_string_value(raw.get("name")) or plugin_id,
        description=_string_value(raw.get("description")),
        version=_string_value(raw.get("version")) or "0.0.0",
        api_version=_int_value(raw.get("api_version"), 1),
        enabled=_bool_value(raw.get("enabled"), True),
        priority=_int_value(raw.get("priority"), 100),
        required=_bool_value(raw.get("required"), False),
        plugin_root=plugin_root,
        source="manifest",
    )


def save_plugin_enabled_overrides(
    base_dir: Path,
    enabled_by_id: dict[str, bool],
    config_path: Path | None = None,
) -> bool:
    """保存插件启用状态覆盖配置，返回配置是否发生变化。"""
    path = config_path or base_dir / "data" / "config" / "plugins.yaml"
    raw = _load_yaml(path)
    entries = list(raw) if isinstance(raw, list) else []
    specs = PluginDiscovery(base_dir, config_path=path).discover()
    by_id: dict[str, dict[str, Any]] = {}
    legacy_entries: list[Any] = []
    for item in entries:
        if not isinstance(item, dict):
            legacy_entries.append(item)
            continue
        plugin_id = _string_value(item.get("id"))
        if plugin_id:
            by_id[plugin_id] = dict(item)
        else:
            legacy_entries.append(dict(item))

    next_entries: list[dict[str, Any] | Any] = []
    seen_ids: set[str] = set()
    for spec in specs:
        if not spec.plugin_id:
            continue
        enabled = enabled_by_id.get(spec.plugin_id, spec.enabled)
        if spec.required:
            enabled = True
        item = by_id.get(spec.plugin_id, {})
        item["id"] = spec.plugin_id
        if spec.source != "manifest" and spec.entry:
            item["entry"] = spec.entry
        item["enabled"] = bool(enabled)
        item["priority"] = int(item.get("priority", spec.priority))
        next_entries.append(item)
        seen_ids.add(spec.plugin_id)

    for plugin_id, item in by_id.items():
        if plugin_id not in seen_ids:
            next_entries.append(item)
    next_entries.extend(legacy_entries)

    path.parent.mkdir(parents=True, exist_ok=True)
    next_text = yaml.safe_dump(next_entries, allow_unicode=True, sort_keys=False)
    previous_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if previous_text == next_text:
        return False
    path.write_text(next_text, encoding="utf-8")
    return True


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        debug_log(
            "PluginDiscovery",
            "插件 YAML 读取失败，已忽略",
            {"path": path, "error": str(exc)},
        )
        return None


def _string_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _plugin_id_from_entry(entry: str) -> str:
    module_name = entry.partition(":")[0]
    parts = module_name.split(".")
    if len(parts) >= 2 and parts[0] == "plugins":
        return parts[1]
    return module_name.rsplit(".", 1)[-1]
