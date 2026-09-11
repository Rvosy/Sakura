"""One-time handoff of the retired bundled Web MCP to the web plugin.

Called before either tool provider starts. Only distribution-owned script paths
are recognized; an external server merely named ``web`` is never rewritten.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.agent.mcp.config import load_mcp_config
from app.core.runtime_log import log_event
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.atomic import atomic_write_text
from app.storage.runtime_roots import RuntimeRoots


PLUGIN_ID = "sakura.web"
SCRIPT = "app/agent/mcp/web_search_server.py"
TOOL_NAMES = ("web_search", "fetch_url")


def prepare_bundled_web_plugin(roots: RuntimeRoots) -> None:
    if (roots.distribution_root / "plugins/builtin/sakura_web/plugin.yaml").is_file():
        migrate_web_configuration(roots.user_root, roots=roots)


def migrate_web_configuration(user_root: Path, *, roots: RuntimeRoots | None = None) -> None:
    desired = PluginDesiredStateStore(user_root)
    switches = desired.read()
    mcp_path = user_root / "config/mcp.yaml"
    config_path = user_root / "data/plugins" / PLUGIN_ID / "config.json"
    try:
        plugin_config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        if not isinstance(plugin_config, dict):
            raise ValueError("WEB_PLUGIN_CONFIG_INVALID")
    except (OSError, UnicodeError, ValueError):
        # The ordinary plugin loader will fail this plugin in isolation. Do
        # not let a corrupt private file take down Core or rewrite old MCP.
        log_event("Config", "联网插件配置无法读取，未执行旧配置交接", {"reason_code": "WEB_PLUGIN_CONFIG_INVALID"})
        return

    def write_config(values: dict) -> None:
        if values != plugin_config:
            atomic_write_text(config_path, json.dumps(values, ensure_ascii=False, indent=2) + "\n")

    def blocked(code: str) -> None:
        write_config({**plugin_config, "migration_error": code})
        if PLUGIN_ID not in switches:
            desired.set(PLUGIN_ID, False)
        log_event("Config", "联网旧配置需要处理；原 MCP 配置已保留", {"reason_code": code})

    try:
        mcp = load_mcp_config(mcp_path)
        raw = yaml.safe_load(mcp_path.read_text(encoding="utf-8")) if mcp_path.exists() else {}
        raw = raw or {}
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        # Existing explicit plugin choices are independent of an unrelated,
        # broken external MCP file after the first successful handoff.
        if PLUGIN_ID not in switches or plugin_config.get("migration_error"):
            blocked("WEB_MIGRATION_CONFIG_INVALID")
        return

    known_paths = {f"{{{root}}}/{SCRIPT}" for root in ("core_root", "base_dir", "distribution_root")}
    if roots is not None:
        known_paths.update({str(roots.distribution_root / "core" / SCRIPT), str(roots.distribution_root / SCRIPT)})
    known_paths = {value.replace("\\", "/").casefold() for value in known_paths}
    candidates = [
        server for server in mcp.servers
        if any(arg.replace("\\", "/").casefold() in known_paths for arg in server.args)
    ]
    if PLUGIN_ID in switches and not candidates and not plugin_config.get("migration_error"):
        return
    # A retained, disabled item has already handed over once the plugin switch
    # exists. Do not overwrite migrated restrictions on every startup.
    if (
        PLUGIN_ID in switches and "allowed_tools" in plugin_config
        and candidates and all(not item.enabled for item in candidates)
        and not plugin_config.get("migration_error")
    ):
        return
    if len(candidates) > 1:
        blocked("WEB_MIGRATION_CUSTOM_BEHAVIOR")
        return

    next_config = dict(plugin_config)
    next_config.pop("migration_error", None)
    enabled = not mcp_path.exists() or mcp.enabled
    if candidates:
        server = candidates[0]
        server_raw = raw["servers"][server.name]
        supported_keys = {
            "transport", "enabled", "command", "args", "env", "url", "headers", "name_prefix",
            "call_timeout", "risk", "include_tools", "exclude_tools", "tool_policies",
        }
        if (
            server.transport != "stdio" or server.command != "{python}"
            or len(server.args) != 1 or server.env or server.url or server.headers
            or server.effective_name_prefix() != "web__"
            or set(server_raw) - supported_keys
        ):
            blocked("WEB_MIGRATION_CUSTOM_BEHAVIOR")
            return
        enabled = mcp.enabled and server.enabled
        next_config.setdefault("allowed_tools", [name for name in TOOL_NAMES if server.allows_tool(name)])
        next_config.setdefault("tool_risks", {name: server.effective_tool_risk(name) for name in TOOL_NAMES})
        next_config.setdefault("call_timeout", server.effective_call_timeout(mcp.default_call_timeout))

    # A pending marker fails the plugin closed if a later write is interrupted.
    # The old MCP remains intact until both plugin data and its switch are saved.
    if candidates:
        pending = {**next_config, "migration_error": "WEB_MIGRATION_INCOMPLETE"}
        write_config(pending)
    if PLUGIN_ID not in switches:
        desired.set(PLUGIN_ID, enabled)
    if candidates:
        raw["servers"][candidates[0].name]["enabled"] = False
        atomic_write_text(mcp_path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), backup=True)
        # Compare against pending rather than the original values here.
        atomic_write_text(config_path, json.dumps(next_config, ensure_ascii=False, indent=2) + "\n")
    else:
        write_config(next_config)
