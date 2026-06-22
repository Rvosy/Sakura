"""app/plugins/discovery.py — 插件发现。

负责扫描 plugins/ 目录和 plugins.yaml 配置，
发现可用插件并解析其清单信息。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from app.plugins.models import PluginSpec
from app.storage.paths import StoragePaths


class PluginDiscovery:
    """从配置文件和插件目录发现可用插件。

    职责：
    - 解析 data/config/plugins.yaml 中的插件入口
    - 按 priority 排序
    - 检查 enabled 状态
    """

    def __init__(self, base_dir: Path, config_path: Path | None = None) -> None:
        self.base_dir = base_dir
        self._config_path = config_path or StoragePaths(base_dir).plugins_config()

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
        overrides = self._load_config_overrides()
        specs: list[PluginSpec] = []
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
        return specs

    def _load_manifest_specs(self) -> list[PluginSpec]:
        plugins_dir = self.base_dir / "plugins"
        if not plugins_dir.is_dir():
            return []
        specs: list[PluginSpec] = []
        for manifest_path in sorted(plugins_dir.glob("*/plugin.yaml")):
            raw = _load_yaml(manifest_path)
            if not isinstance(raw, dict):
                continue
            spec = _spec_from_manifest(raw, manifest_path.parent)
            if spec is not None:
                specs.append(spec)
        return specs

    def _load_config_overrides(self) -> dict[str, PluginSpec]:
        raw = _load_yaml(self._config_path)
        if not isinstance(raw, list):
            return {}
        overrides: dict[str, PluginSpec] = {}
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            plugin_id = _string_value(item.get("id"))
            if not plugin_id:
                continue
            priority = _int_value(item.get("priority"), 100 - idx)
            priority_override = "priority" in item
            enabled = _bool_value(item.get("enabled"), True)
            required = _bool_value(item.get("required"), False)
            overrides[plugin_id] = PluginSpec(
                entry="",
                plugin_id=plugin_id,
                enabled=enabled,
                priority=priority,
                required=required,
                description=_string_value(item.get("description")),
                source="config",
                priority_override=priority_override,
            )
        return overrides


def _spec_from_manifest(raw: dict[str, Any], plugin_root: Path) -> PluginSpec | None:
    plugin_id = _string_value(raw.get("id") or raw.get("plugin_id"))
    entry = _string_value(raw.get("entry"))
    if not plugin_id or not entry:
        return None
    return PluginSpec(
        entry=entry,
        plugin_id=plugin_id,
        name=_string_value(raw.get("name")) or plugin_id,
        author=_string_value(raw.get("author")),
        description=_string_value(raw.get("description")),
        version=_string_value(raw.get("version")) or "0.0.0",
        api_version=_int_value(raw.get("api_version"), 0),
        enabled=_bool_value(raw.get("enabled"), True),
        priority=_int_value(raw.get("priority"), 100),
        required=_bool_value(raw.get("required"), False),
        permissions=_permissions_value(raw.get("permissions")),
        plugin_root=plugin_root,
        source="manifest",
    )


def save_plugin_enabled_overrides(
    base_dir: Path,
    enabled_by_id: dict[str, bool],
    config_path: Path | None = None,
) -> bool:
    """保存插件启用状态覆盖配置，返回配置是否发生变化。"""
    path = config_path or StoragePaths(base_dir).plugins_config()
    raw = _load_yaml(path)
    entries = list(raw) if isinstance(raw, list) else []
    specs = PluginDiscovery(base_dir, config_path=path).discover()
    by_id: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        plugin_id = _string_value(item.get("id"))
        if plugin_id:
            by_id[plugin_id] = dict(item)

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
        item["enabled"] = bool(enabled)
        item["priority"] = int(item.get("priority", spec.priority))
        next_entries.append(item)
        seen_ids.add(spec.plugin_id)

    for plugin_id, item in by_id.items():
        if plugin_id not in seen_ids:
            next_entries.append(item)

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
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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


def _permissions_value(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    permissions: list[str] = []
    for item in value:
        text = _string_value(item)
        if text:
            permissions.append(text)
    return tuple(dict.fromkeys(permissions))
