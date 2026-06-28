"""app/config/default_configs.py — 运行时生成默认配置。

mcp.yaml / plugins.yaml 不再随发布包携带（否则覆盖升级会用默认值
覆盖用户修改过的配置），改为首次启动/文件缺失时在此生成。
已存在的文件只补齐缺失的内置默认项，不覆盖用户已有配置。
"""

from __future__ import annotations

from pathlib import Path

from app.core.debug_log import debug_log
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths

# 与历史发布版本默认 mcp.yaml 等价的内容（web 搜索开启、Windows-MCP 关闭）
_DEFAULT_MCP_YAML = """\
enabled: true
default_call_timeout: 20
servers:
  web:
    transport: stdio
    command: "{python}"
    args: ["{base_dir}/app/agent/mcp/web_search_server.py"]
    name_prefix: web__
    risk: low
    requires_confirmation: false
  windows:
    enabled: false
    transport: stdio
    command: "{uv}"
    args:
      - "--directory"
      - "{base_dir}/tools/mcp/Windows-MCP-0.8.0"
      - "run"
      - "windows-mcp"
      - "serve"
      - "--tools"
      - "App,Snapshot,Screenshot,Click,Type,Wait"
    env:
      ANONYMIZED_TELEMETRY: "false"
      WINDOWS_MCP_TOOLS: "App,Snapshot,Screenshot,Click,Type,Wait"
      WINDOWS_MCP_EXCLUDE_TOOLS: "PowerShell,Registry,Process,FileSystem,Clipboard,Scrape,MultiSelect,MultiEdit,Notification,Scroll,Move,Shortcut"
    name_prefix: windows__
    call_timeout: 30
    risk: high
    requires_confirmation: true
    include_tools:
      - App
      - Snapshot
      - Screenshot
      - Click
      - Type
      - Wait
    exclude_tools:
      - PowerShell
      - Registry
      - Process
      - FileSystem
      - Clipboard
      - Scrape
      - MultiSelect
      - MultiEdit
      - Notification
      - Scroll
      - Move
      - Shortcut
    tool_policies:
      Snapshot:
        risk: medium
        requires_confirmation: false
      Screenshot:
        risk: medium
        requires_confirmation: false
  macos:
    enabled: false
    transport: stdio
    command: "{uvx}"
    args:
      - "macos-mcp"
    env:
      ANONYMIZED_TELEMETRY: "false"
    name_prefix: macos__
    call_timeout: 30
    risk: high
    requires_confirmation: true
    include_tools:
      - App
      - Snapshot
      - Click
      - Type
      - Wait
    exclude_tools:
      - Shell
      - Scrape
      - Notification
      - Move
      - Scroll
      - Shortcut
    tool_policies:
      Snapshot:
        risk: medium
        requires_confirmation: false
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
                    _backfill_mcp_config(target)
                continue
            atomic_write_text(target, content, encoding="utf-8", backup=False)
            created.append(target.name)
        except OSError as exc:
            debug_log(
                "Config",
                "默认配置生成失败",
                {"path": str(target), "error": str(exc)},
            )
    if created:
        debug_log("Config", "默认配置已生成", {"created": created})
    return created


def _backfill_mcp_config(path: Path) -> None:
    try:
        import yaml
    except ImportError as exc:
        debug_log("Config", "默认 MCP 配置补齐失败", {"path": str(path), "error": str(exc)})
        return

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        defaults = yaml.safe_load(_DEFAULT_MCP_YAML)
    except (OSError, yaml.YAMLError) as exc:
        debug_log("Config", "默认 MCP 配置补齐失败", {"path": str(path), "error": str(exc)})
        return
    if not isinstance(data, dict) or not isinstance(defaults, dict):
        return
    servers = data.get("servers")
    default_servers = defaults.get("servers")
    if not isinstance(servers, dict) or not isinstance(default_servers, dict):
        return
    if "macos" in servers or not {"web", "windows"}.issubset(servers):
        return
    macos_server = default_servers.get("macos")
    if not isinstance(macos_server, dict):
        return
    servers["macos"] = macos_server
    try:
        atomic_write_text(
            path,
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
            backup=True,
        )
    except OSError as exc:
        debug_log("Config", "默认 MCP 配置补齐失败", {"path": str(path), "error": str(exc)})
