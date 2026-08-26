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
from app.plugins.inventory import PluginDesiredStateStore
from app.core.runtime_log import log_event
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import DistributionPaths, RuntimeRoots, coerce_runtime_roots


class PluginDiscovery:
    """从配置文件和插件目录发现可用插件。

    职责：
    - 解析 user_root/config/plugins.yaml 中的插件入口
    - 按 priority 排序
    - 检查 enabled 状态
    """

    def __init__(
        self,
        roots: RuntimeRoots | Path,
        config_path: Path | None = None,
    ) -> None:
        self.roots = coerce_runtime_roots(roots)
        self.base_dir = self.roots.user_root
        self._distribution = DistributionPaths(self.roots.distribution_root)
        self._config_path = config_path or StoragePaths(self.base_dir).plugins_config()

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
        overrides = PluginDesiredStateStore(
            self.base_dir,
            self._config_path,
        ).read()
        specs: list[PluginSpec] = []
        for spec in manifest_specs:
            enabled = overrides.get(spec.plugin_id, spec.enabled)
            spec = replace(
                spec,
                enabled=True if spec.required and spec.source == "bundled" else enabled,
                required=bool(spec.required and spec.source == "bundled"),
                priority_override=False,
            )
            specs.append(spec)
        return specs

    def _load_manifest_specs(self) -> list[PluginSpec]:
        specs: list[PluginSpec] = []
        manifest_paths = [
            (path, "bundled")
            for path in sorted(self._distribution.builtin_plugins_dir.glob("*/plugin.yaml"))
        ]
        manifest_paths.extend(
            (path, "user")
            for path in sorted(StoragePaths(self.base_dir).user_plugins_dir.glob("*/plugin.yaml"))
        )
        for manifest_path, source in manifest_paths:
            try:
                raw = _load_yaml(manifest_path)
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
                log_event(
                    "PluginDiscovery",
                    "跳过损坏的插件清单",
                    {"path": str(manifest_path), "error": str(exc)},
                )
                continue
            if not isinstance(raw, dict):
                continue
            spec = plugin_spec_from_manifest(raw, manifest_path.parent, source=source)
            if spec is not None:
                specs.append(spec)
        return specs

    def _load_config_overrides(self) -> dict[str, PluginSpec]:
        try:
            raw = _load_yaml(self._config_path)
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
            log_event(
                "PluginDiscovery",
                "插件覆盖配置损坏，忽略覆盖",
                {"path": str(self._config_path), "error": str(exc)},
            )
            return {}
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


def plugin_spec_from_manifest(
    raw: dict[str, Any],
    plugin_root: Path,
    *,
    source: str = "manifest",
) -> PluginSpec | None:
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
        api_version=_int_value(raw.get("api_version", raw.get("api")), 0),
        enabled=_bool_value(raw.get("enabled"), True),
        priority=_int_value(raw.get("priority"), 100),
        required=_bool_value(raw.get("required"), False),
        permissions=_permissions_value(raw.get("permissions")),
        provides=_service_keys_value(raw.get("provides")),
        requires=_service_keys_value(raw.get("requires")),
        plugin_root=plugin_root,
        source=source,
    )


def save_plugin_enabled_overrides(
    base_dir: Path,
    enabled_by_id: dict[str, bool],
    config_path: Path | None = None,
) -> bool:
    """Compatibility wrapper around the Core-owned canonical writer."""

    return PluginDesiredStateStore(base_dir, config_path).write(enabled_by_id)


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


def _service_keys_value(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    keys: list[str] = []
    for item in value:
        text = _string_value(item)
        if text:
            keys.append(text)
    return tuple(dict.fromkeys(keys))
