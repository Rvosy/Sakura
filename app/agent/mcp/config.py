from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEFAULT_MCP_CALL_TIMEOUT_SECONDS = 30.0
SUPPORTED_MCP_TRANSPORTS = {"stdio", "sse"}
SUPPORTED_MCP_RISKS = {"low", "medium", "high"}
MAX_MCP_CONFIG_BYTES = 256 * 1024
MAX_MCP_SERVERS = 16
MAX_MCP_SERVER_NAME_CHARS = 64
MAX_MCP_ARGUMENTS = 64
MAX_MCP_MAPPING_ITEMS = 128
MAX_MCP_VALUE_CHARS = 8 * 1024
MAX_MCP_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class MCPToolPolicy:
    """单个 MCP 工具的风险覆盖；确认字段仅为旧配置解析保留。"""

    risk: str | None = None
    requires_confirmation: bool | None = None


@dataclass(frozen=True)
class MCPServerConfig:
    """单个 MCP Server 的连接和工具暴露配置。"""

    name: str
    transport: str
    enabled: bool = True
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    name_prefix: str | None = None
    call_timeout: float | None = None
    risk: str = "medium"
    requires_confirmation: bool | None = None
    include_tools: list[str] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=list)
    tool_policies: dict[str, MCPToolPolicy] = field(default_factory=dict)

    def effective_name_prefix(self) -> str:
        if self.name_prefix is not None:
            return self.name_prefix
        normalized = "".join(char if char.isalnum() or char == "_" else "_" for char in self.name)
        return f"{normalized}__"

    def effective_call_timeout(self, default_timeout: float) -> float:
        return self.call_timeout if self.call_timeout is not None else default_timeout

    def allows_tool(self, tool_name: str) -> bool:
        """按白名单/黑名单判断是否暴露指定 MCP 工具。"""

        if self.include_tools and not _matches_any_tool(tool_name, self.include_tools):
            return False
        if self.exclude_tools and _matches_any_tool(tool_name, self.exclude_tools):
            return False
        return True

    def effective_tool_risk(self, tool_name: str) -> str:
        policy = self._matching_tool_policy(tool_name)
        if policy is not None and policy.risk is not None:
            return policy.risk
        return self.risk

    def _matching_tool_policy(self, tool_name: str) -> MCPToolPolicy | None:
        best_policy: MCPToolPolicy | None = None
        best_score: tuple[int, int, int] | None = None
        for index, (pattern, policy) in enumerate(self.tool_policies.items()):
            if not _tool_pattern_matches(tool_name, pattern):
                continue
            exact_score = 1 if _is_exact_tool_pattern(pattern) else 0
            score = (exact_score, len(pattern), index)
            if best_score is None or score > best_score:
                best_score = score
                best_policy = policy
        return best_policy


@dataclass(frozen=True)
class MCPConfig:
    """MCP 总配置；文件不存在时保持禁用，避免影响主流程。"""

    enabled: bool = False
    default_call_timeout: float = DEFAULT_MCP_CALL_TIMEOUT_SECONDS
    servers: list[MCPServerConfig] = field(default_factory=list)


def load_mcp_config(path: Path) -> MCPConfig:
    """读取 data/config/mcp.yaml；不存在时静默禁用 MCP。"""

    if not path.exists():
        return MCPConfig()

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("缺少 PyYAML，无法读取 MCP 配置。请安装 requirements.txt。") from exc

    encoded = path.read_bytes()
    if len(encoded) > MAX_MCP_CONFIG_BYTES:
        raise ValueError("MCP 配置超过允许大小。")
    raw = yaml.safe_load(encoded.decode("utf-8"))
    if raw is None:
        return MCPConfig()
    if not isinstance(raw, dict):
        raise ValueError("MCP 配置顶层必须是 YAML object。")

    enabled = _optional_bool(raw.get("enabled"), default=True)
    default_timeout = _optional_positive_float(
        raw.get("default_call_timeout"),
        DEFAULT_MCP_CALL_TIMEOUT_SECONDS,
        "default_call_timeout",
    )
    servers = _parse_servers(raw.get("servers"), default_timeout)
    return MCPConfig(
        enabled=enabled,
        default_call_timeout=default_timeout,
        servers=servers,
    )


def _parse_servers(raw_servers: Any, default_timeout: float) -> list[MCPServerConfig]:
    if raw_servers is None:
        return []

    items: list[tuple[str, Any]] = []
    if isinstance(raw_servers, dict):
        items = [(str(name), value) for name, value in raw_servers.items()]
    elif isinstance(raw_servers, list):
        for index, item in enumerate(raw_servers):
            if not isinstance(item, dict):
                raise ValueError(f"servers[{index}] 必须是 YAML object。")
            name = item.get("name") or item.get("id") or f"server_{index + 1}"
            items.append((str(name), item))
    else:
        raise ValueError("servers 必须是 object 或 list。")
    if len(items) > MAX_MCP_SERVERS:
        raise ValueError("MCP Server 数量超过允许上限。")
    normalized_names: set[str] = set()
    servers: list[MCPServerConfig] = []
    for name, data in items:
        normalized = name.strip()
        if not normalized or len(normalized) > MAX_MCP_SERVER_NAME_CHARS:
            raise ValueError("MCP Server 名称无效。")
        folded = normalized.casefold()
        if folded in normalized_names:
            raise ValueError("MCP Server 名称重复。")
        normalized_names.add(folded)
        servers.append(_parse_server(normalized, data, default_timeout))
    return servers


def _parse_server(name: str, data: Any, default_timeout: float) -> MCPServerConfig:
    if not isinstance(data, dict):
        raise ValueError(f"MCP Server {name} 配置必须是 YAML object。")

    transport = str(data.get("transport") or data.get("type") or "").strip().lower()
    if not transport:
        transport = "sse" if data.get("url") else "stdio"
    if transport not in SUPPORTED_MCP_TRANSPORTS:
        raise ValueError(f"MCP Server {name} transport 只支持 stdio 或 sse。")

    risk = _optional_risk(data.get("risk"), f"{name}.risk") or "medium"

    call_timeout = data.get("call_timeout")
    parsed_call_timeout = (
        None
        if call_timeout is None
        else _optional_positive_float(call_timeout, default_timeout, f"{name}.call_timeout")
    )
    include_tools, exclude_tools, tool_policies = _parse_tool_settings(name, data)

    command = _bounded_string(data.get("command"), f"{name}.command")
    url = _bounded_string(data.get("url"), f"{name}.url")
    if transport == "stdio" and not command:
        raise ValueError(f"MCP Server {name} 缺少 command。")
    if transport == "sse":
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"MCP Server {name} URL 无效。")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(f"MCP Server {name} URL 不允许包含 userinfo。")

    return MCPServerConfig(
        name=name.strip(),
        transport=transport,
        enabled=_optional_bool(data.get("enabled"), default=True),
        command=command,
        args=_string_list(data.get("args"), f"{name}.args"),
        env=_string_dict(data.get("env"), f"{name}.env"),
        url=url,
        headers=_string_dict(data.get("headers"), f"{name}.headers"),
        name_prefix=_optional_string(data.get("name_prefix")),
        call_timeout=parsed_call_timeout,
        risk=risk,
        requires_confirmation=_optional_bool_or_none(data.get("requires_confirmation")),
        include_tools=include_tools,
        exclude_tools=exclude_tools,
        tool_policies=tool_policies,
    )


def _optional_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError("布尔配置项必须是 true 或 false。")


def _optional_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return _optional_bool(value, default=False)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("字符串配置项必须是 string。")


def _optional_risk(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    risk = str(value).strip().lower()
    if risk not in SUPPORTED_MCP_RISKS:
        raise ValueError(f"{field_name} 只支持 low、medium 或 high。")
    return risk


def _optional_positive_float(value: Any, default: float, field_name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是正数。")
    result = float(value)
    if result <= 0 or result > MAX_MCP_TIMEOUT_SECONDS:
        raise ValueError(f"{field_name} 必须大于 0 且不超过 {MAX_MCP_TIMEOUT_SECONDS:g}。")
    return result


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是 list。")
    if len(value) > MAX_MCP_ARGUMENTS:
        raise ValueError(f"{field_name} 项数超过允许上限。")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} 只能包含字符串。")
        if len(item) > MAX_MCP_VALUE_CHARS:
            raise ValueError(f"{field_name} 包含过长字符串。")
        result.append(item)
    return result


def _string_dict(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 object。")
    if len(value) > MAX_MCP_MAPPING_ITEMS:
        raise ValueError(f"{field_name} 项数超过允许上限。")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{field_name} 的键和值都必须是字符串。")
        if len(key) > 256 or len(item) > MAX_MCP_VALUE_CHARS:
            raise ValueError(f"{field_name} 包含过长键或值。")
        result[key] = item
    return result


def _parse_tool_settings(
    server_name: str,
    data: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, MCPToolPolicy]]:
    tools_block = data.get("tools")
    if tools_block is not None and not isinstance(tools_block, (dict, list)):
        raise ValueError(f"{server_name}.tools 必须是 object 或 list。")

    tools_dict = tools_block if isinstance(tools_block, dict) else {}
    include_source = data.get("include_tools")
    if include_source is None:
        include_source = data.get("allow_tools")
    if include_source is None and isinstance(tools_block, list):
        include_source = tools_block
    if include_source is None:
        include_source = tools_dict.get("include")
    if include_source is None:
        include_source = tools_dict.get("allow")

    exclude_source = data.get("exclude_tools")
    if exclude_source is None:
        exclude_source = data.get("deny_tools")
    if exclude_source is None:
        exclude_source = tools_dict.get("exclude")
    if exclude_source is None:
        exclude_source = tools_dict.get("deny")

    policies_source = data.get("tool_policies")
    if policies_source is None:
        policies_source = tools_dict.get("policies")

    include_tools = _string_list(include_source, f"{server_name}.include_tools")
    exclude_tools = _string_list(exclude_source, f"{server_name}.exclude_tools")
    tool_policies = _parse_tool_policies(policies_source, f"{server_name}.tool_policies")
    return include_tools, exclude_tools, tool_policies


def _parse_tool_policies(value: Any, field_name: str) -> dict[str, MCPToolPolicy]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 object。")

    result: dict[str, MCPToolPolicy] = {}
    for pattern, raw_policy in value.items():
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"{field_name} 的工具名必须是非空字符串。")
        policy_name = pattern.strip()
        if raw_policy is None:
            result[policy_name] = MCPToolPolicy()
            continue
        if not isinstance(raw_policy, dict):
            raise ValueError(f"{field_name}.{policy_name} 必须是 object。")
        result[policy_name] = MCPToolPolicy(
            risk=_optional_risk(raw_policy.get("risk"), f"{field_name}.{policy_name}.risk"),
            requires_confirmation=_optional_bool_or_none(raw_policy.get("requires_confirmation")),
        )
    return result


def _matches_any_tool(tool_name: str, patterns: list[str]) -> bool:
    return any(_tool_pattern_matches(tool_name, pattern) for pattern in patterns)


def _tool_pattern_matches(tool_name: str, pattern: str) -> bool:
    return fnmatchcase(tool_name.lower(), pattern.lower())


def _is_exact_tool_pattern(pattern: str) -> bool:
    return not any(char in pattern for char in "*?[]")


def _bounded_string(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是 string。")
    normalized = value.strip()
    if len(normalized) > MAX_MCP_VALUE_CHARS:
        raise ValueError(f"{field_name} 超过允许长度。")
    return normalized
