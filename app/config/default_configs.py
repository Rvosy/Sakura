"""app/config/default_configs.py — 运行时生成默认配置。

mcp.yaml / plugins.yaml 不再随发布包携带（否则覆盖升级会用默认值
覆盖用户修改过的配置），改为首次启动/文件缺失时在此生成。
已存在的文件只清理已退役的内置项，不覆盖其他用户配置。
"""

from __future__ import annotations

from pathlib import Path

from app.core.runtime_log import diagnostic_attributes, log_event
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths

# 内置 MCP 默认配置只提供 Web 搜索。
_DEFAULT_MCP_YAML = """\
enabled: true
default_call_timeout: 20
servers:
  web:
    transport: stdio
    command: "{python}"
    args: ["{core_root}/app/agent/mcp/web_search_server.py"]
    name_prefix: web__
    risk: low
"""

# 内置插件的默认启停（与各插件 plugin.yaml 的 manifest 默认一致）
_DEFAULT_PLUGINS_YAML = """\
- id: playwright_browser
  enabled: true
  priority: 40
- id: example_plugin
  enabled: false
  priority: 30
"""


def ensure_default_configs(base_dir: Path) -> list[str]:
    """缺失的默认配置文件落盘；返回本次生成的文件名列表。"""
    paths = StoragePaths(base_dir)
    created: list[str] = []
    for target, content in (
        (paths.mcp_config(), _DEFAULT_MCP_YAML),
        (paths.plugins_config(), _DEFAULT_PLUGINS_YAML),
    ):
        try:
            if target.exists():
                if target == paths.mcp_config():
                    _sync_builtin_mcp_config(target)
                continue
            atomic_write_text(target, content, encoding="utf-8", backup=False)
            created.append(target.name)
        except OSError as exc:
            log_event(
                "Config",
                "默认配置生成失败",
                {
                    "path": str(target),
                    **diagnostic_attributes(
                        exc,
                        reason_code="DEFAULT_CONFIG_WRITE_FAILED",
                        stage="default_config_write",
                    ),
                },
            )
    if created:
        log_event("Config", "默认配置已生成", {"created": created})
    return created


def _sync_builtin_mcp_config(path: Path) -> None:
    try:
        import yaml
    except ImportError as exc:
        log_event(
            "Config",
            "默认 MCP 配置补齐失败",
            {
                "path": str(path),
                **diagnostic_attributes(
                    exc,
                    reason_code="DEFAULT_MCP_CONFIG_FAILED",
                    stage="yaml_import",
                ),
            },
        )
        return

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        defaults = yaml.safe_load(_DEFAULT_MCP_YAML)
    except (OSError, yaml.YAMLError) as exc:
        log_event(
            "Config",
            "默认 MCP 配置补齐失败",
            {
                "path": str(path),
                **diagnostic_attributes(
                    exc,
                    reason_code="DEFAULT_MCP_CONFIG_FAILED",
                    stage="config_load",
                ),
            },
        )
        return
    if not isinstance(data, dict) or not isinstance(defaults, dict):
        return
    servers = data.get("servers")
    default_servers = defaults.get("servers")
    if not isinstance(servers, dict) or not isinstance(default_servers, dict):
        return
    changed = servers.pop("windows", None) is not None
    web = servers.get("web")
    if isinstance(web, dict) and web.get("args") in (
        ["{base_dir}/app/agent/mcp/web_search_server.py"],
        ["{distribution_root}/app/agent/mcp/web_search_server.py"],
    ):
        web["args"] = ["{core_root}/app/agent/mcp/web_search_server.py"]
        changed = True
    if not changed:
        return
    try:
        atomic_write_text(
            path,
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
            backup=True,
        )
    except OSError as exc:
        log_event(
            "Config",
            "默认 MCP 配置补齐失败",
            {
                "path": str(path),
                **diagnostic_attributes(
                    exc,
                    reason_code="DEFAULT_MCP_CONFIG_FAILED",
                    stage="config_write",
                ),
            },
        )
