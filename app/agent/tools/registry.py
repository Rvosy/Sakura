"""统一工具注册表 — 保存、描述、执行工具的唯一入口。

本模块定义：
- Tool: 工具定义数据类
- ToolMetadata: 统一的工具元数据
- ToolExecutionResult: 工具执行结果
- ToolRegistry: 工具注册表（保存/描述/执行）
- ToolHandler: 工具处理器类型
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent.actions import PendingToolAction
from app.core.debug_log import debug_log


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolMetadata:
    """统一的工具元数据。

    每个工具必须提供完整的元数据，用于：
    - 工具的搜索与发现
    - 权限与风险控制
    - 模型工具列表生成
    """

    name: str
    description: str
    group: str = "default"
    risk: str = "low"
    capability: str | None = None
    source: str = "builtin"

    @classmethod
    def from_tool(cls, tool: "Tool") -> "ToolMetadata":
        """从 Tool 实例构造元数据。"""
        return cls(
            name=tool.name,
            description=tool.description,
            group=tool.group,
            risk=tool.risk,
            capability=tool.capability,
        )


@dataclass(frozen=True)
class Tool:
    """内部工具定义。"""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: ToolHandler | None = None
    requires_confirmation: bool = False
    confirmation_risk: str = "normal"
    group: str = "default"
    risk: str = "low"
    capability: str | None = None
    source: str = "builtin"

    @property
    def metadata(self) -> ToolMetadata:
        """返回此工具的统一元数据。"""
        return ToolMetadata.from_tool(self)


@dataclass(frozen=True)
class ToolExecutionResult:
    """工具执行结果，统一交回模型做最终表述。"""

    tool_name: str
    success: bool
    content: Any
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tool_name": self.tool_name,
            "success": self.success,
            "content": self.content,
        }
        if self.error:
            data["error"] = self.error
        return data


class ToolRegistry:
    """管理 Agent 可用工具的注册表。

    职责：
    - 保存工具定义 (register / get / all)
    - 按条件描述工具 (describe_tools / describe_openai_tools)
    - 执行工具 (execute / prepare_or_execute)
    - 工具搜索 (search_tools / list_tool_groups)
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        from app.agent.tools.permission_policy import ToolPermissionPolicy
        self.permission_policy = ToolPermissionPolicy()
        # 可选事件发射器（由宿主注入），用于派发 tool.* 插件事件。
        self._event_emit: Callable[[str, dict[str, Any] | None], None] | None = None
        for tool in tools or []:
            self.register(tool)

    def set_event_emitter(
        self,
        emitter: Callable[[str, dict[str, Any] | None], None] | None,
    ) -> None:
        """注入插件事件发射器；传 None 关闭。"""
        self._event_emit = emitter

    def _emit_tool_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """安全派发工具事件，发射器异常不影响工具执行。"""
        emitter = self._event_emit
        if emitter is None:
            return
        try:
            emitter(event_name, payload)
        except Exception:  # noqa: BLE001 — 事件派发不得影响工具执行
            pass

    # ---- 注册 ----

    def register(self, tool: Tool) -> None:
        """注册一个工具。同名工具会覆盖旧的。"""
        self._tools[tool.name] = tool
        debug_log(
            "ToolRegistry",
            "注册工具",
            {
                "name": tool.name,
                "group": tool.group,
                "risk": tool.risk,
                "requires_confirmation": tool.requires_confirmation,
                "source": tool.source,
                "capability": tool.capability,
            },
        )

    def register_from_provider(self, provider: object) -> int:
        """从 ToolProvider 批量注册工具。

        返回成功注册的数量。
        """
        contribute = getattr(provider, "contribute_tools", None)
        if contribute is None:
            debug_log(
                "ToolRegistry",
                "Provider 不支持 contribute_tools",
                {"provider": type(provider).__name__},
            )
            return 0
        tools = contribute()
        for tool in tools:
            self.register(tool)
        return len(tools)

    # ---- 查询 ----

    @property
    def free_access_enabled(self) -> bool:
        """[向后兼容] 委托给 permission_policy。"""
        return self.permission_policy.free_access_enabled

    @free_access_enabled.setter
    def free_access_enabled(self, enabled: bool) -> None:
        self.permission_policy.free_access_enabled = enabled

    def set_free_access_enabled(self, enabled: bool) -> None:
        """[向后兼容] 设置自由访问模式。"""
        self.free_access_enabled = enabled

    def all(self) -> list[Tool]:
        """返回所有已注册工具。"""
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        """按名称获取工具。"""
        return self._tools.get(name)

    def groups(self) -> set[str]:
        """返回所有工具组。"""
        return {tool.group for tool in self.all()}

    # ---- 描述 (模型可见) ----

    def describe_tools(
        self,
        allowed_capabilities: set[str] | None = None,
        active_groups: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回可暴露给模型的工具描述；可按能力开关和工具组隐藏工具。"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "requires_confirmation": tool.requires_confirmation,
                "group": tool.group,
                "risk": tool.risk,
            }
            for tool in self.all()
            if self._tool_is_visible(
                tool,
                allowed_capabilities=allowed_capabilities,
                active_groups=active_groups,
            )
        ]

    def describe_openai_tools(
        self,
        allowed_capabilities: set[str] | None = None,
        active_groups: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回 OpenAI Chat Completions 原生 function tools 定义。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": _normalize_parameters_schema(tool.parameters),
                },
            }
            for tool in self.all()
            if self._tool_is_visible(
                tool,
                allowed_capabilities=allowed_capabilities,
                active_groups=active_groups,
            )
        ]

    def _tool_is_visible(
        self,
        tool: Tool,
        *,
        allowed_capabilities: set[str] | None,
        active_groups: set[str] | None,
    ) -> bool:
        """判断工具是否应在当前条件下可见。"""
        capability_visible = (
            allowed_capabilities is None
            or tool.capability is None
            or tool.capability in allowed_capabilities
        )
        if not capability_visible:
            return False
        if active_groups is None:
            return True
        if tool.capability is not None and allowed_capabilities and tool.capability in allowed_capabilities:
            return True
        return tool.group in active_groups

    # ---- 搜索 ----

    def search_tools(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """按关键词搜索工具。"""
        keyword = str(arguments.get("keyword") or "").strip().lower()
        results: list[dict[str, Any]] = []
        for tool in self.all():
            if tool.name in {"search_tools", "list_tool_groups"}:
                continue
            if keyword and not _tool_matches_keyword(tool, keyword):
                continue
            results.append(
                {
                    "name": tool.name,
                    "group": tool.group,
                    "description": tool.description,
                    "risk": tool.risk,
                    "requires_confirmation": tool.requires_confirmation,
                    "source": tool.source,
                    "capability": tool.capability,
                }
            )
        return results

    def list_tool_groups(self, _arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """列出所有工具组及数量。"""
        counts: dict[str, int] = {}
        for tool in self.all():
            counts[tool.group] = counts.get(tool.group, 0) + 1
        return [
            {"group": group, "tool_count": count}
            for group, count in sorted(counts.items())
        ]

    # ---- 执行 ----

    def prepare_or_execute(
        self,
        name: str,
        arguments: dict[str, Any],
        reason: str = "",
        tool_call_id: str = "",
        permission_policy: object | None = None,
    ) -> ToolExecutionResult | PendingToolAction:
        """准备执行工具：若需确认则返回 PendingToolAction，否则直接执行。

        permission_policy 参数接受 ToolPermissionPolicy 实例，用于集中控制确认逻辑。
        """
        tool = self.get(name)
        debug_log(
            "ToolRegistry",
            "准备工具执行",
            {
                "name": name,
                "known": tool is not None,
                "arguments": arguments,
                "reason": reason,
            },
        )
        if tool is None:
            return self.execute(name, arguments)

        # 委托给 permission_policy 决定是否需要确认
        policy = permission_policy if permission_policy is not None else self.permission_policy
        if hasattr(policy, "requires_confirmation"):
            needs_confirmation = policy.requires_confirmation(tool, arguments)
        else:
            needs_confirmation = tool.requires_confirmation

        if not needs_confirmation:
            return self.execute(name, arguments)

        if not isinstance(arguments, dict):
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error="工具参数必须是 JSON object。",
            )
            debug_log("ToolRegistry", "工具参数无效", result.to_dict())
            return result

        action = PendingToolAction.create(
            tool_name=name,
            arguments=arguments,
            reason=reason,
            tool_call_id=tool_call_id,
        )
        debug_log("ToolRegistry", "工具等待用户确认", action.to_dict())
        return action

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        """执行一个已注册的工具。"""
        started_at = time.perf_counter()
        tool = self.get(name)
        if tool is None:
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error=f"未知工具：{name}",
            )
            debug_log("ToolRegistry", "工具执行失败", _result_with_elapsed(result, started_at))
            return result
        if tool.handler is None:
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error=f"工具未配置处理器：{name}",
            )
            debug_log("ToolRegistry", "工具执行失败", _result_with_elapsed(result, started_at))
            return result
        if not isinstance(arguments, dict):
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error="工具参数必须是 JSON object。",
            )
            debug_log("ToolRegistry", "工具执行失败", _result_with_elapsed(result, started_at))
            return result

        try:
            debug_log(
                "ToolRegistry",
                "开始执行工具",
                {
                    "name": name,
                    "group": tool.group,
                    "risk": tool.risk,
                    "arguments": arguments,
                },
            )
            self._emit_tool_event(
                "tool.started",
                {"name": name, "group": tool.group, "risk": tool.risk},
            )
            content = tool.handler(arguments)
        except Exception as exc:
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error=str(exc),
            )
            debug_log("ToolRegistry", "工具执行异常", _result_with_elapsed(result, started_at))
            self._emit_tool_event("tool.failed", {"name": name, "error": str(exc)})
            return result
        result = ToolExecutionResult(
            tool_name=name,
            success=True,
            content=content,
        )
        debug_log("ToolRegistry", "工具执行成功", _result_with_elapsed(result, started_at))
        self._emit_tool_event("tool.finished", {"name": name})
        return result


# ---- schema 规范化 ----

def _tool_matches_keyword(tool: Tool, keyword: str) -> bool:
    haystack = "\n".join([tool.name, tool.group, tool.description]).lower()
    return keyword in haystack


def _normalize_parameters_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    if not parameters:
        return {"type": "object", "properties": {}, "required": []}
    if parameters.get("type") == "object":
        schema = dict(parameters)
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        normalized = _sanitize_openai_schema(schema)
        return normalized if isinstance(normalized, dict) else {"type": "object", "properties": {}, "required": []}
    return {
        "type": "object",
        "properties": _sanitize_openai_schema_properties(dict(parameters)),
        "required": [],
    }


def _sanitize_openai_schema(schema: Any) -> Any:
    """把内部 JSON Schema 收窄成兼容常见 OpenAI-compatible 端点的 function schema。"""
    if isinstance(schema, list):
        return [_sanitize_openai_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            sanitized[key] = _sanitize_openai_schema_properties(value)
            continue
        if key == "required" and isinstance(value, list):
            sanitized[key] = [item for item in value if isinstance(item, str)]
            continue
        if key == "type":
            normalized_type = _sanitize_schema_type(value)
            if normalized_type is None:
                continue
            sanitized[key] = normalized_type
            if isinstance(value, list) and "null" in value:
                sanitized["nullable"] = True
            continue
        sanitized[key] = _sanitize_openai_schema(value)

    if "properties" in sanitized and isinstance(sanitized["properties"], dict):
        required = sanitized.get("required")
        if isinstance(required, list):
            sanitized["required"] = [
                item for item in required
                if item in sanitized["properties"]
            ]
    if "type" not in sanitized:
        if "properties" in sanitized:
            sanitized["type"] = "object"
        elif "items" in sanitized:
            sanitized["type"] = "array"
        elif "enum" in sanitized:
            sanitized["type"] = "string"
    return sanitized


def _sanitize_openai_schema_properties(properties: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for name, property_schema in properties.items():
        if _is_null_only_schema(property_schema):
            continue
        sanitized_property = _sanitize_openai_schema(property_schema)
        if isinstance(sanitized_property, dict):
            sanitized[str(name)] = sanitized_property
    return sanitized


def _sanitize_schema_type(value: Any) -> str | None:
    if isinstance(value, str):
        return None if value == "null" else value
    if isinstance(value, list):
        non_null_types = [
            item for item in value if isinstance(item, str) and item != "null"
        ]
        if not non_null_types:
            return None
        return non_null_types[0]
    return None


def _is_null_only_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    return schema_type == "null" or schema_type == ["null"]


def _result_with_elapsed(result: ToolExecutionResult, started_at: float) -> dict[str, Any]:
    data = result.to_dict()
    data["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    return data
