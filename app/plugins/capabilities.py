"""app/plugins/capabilities.py — Sakura 插件能力收集注册表。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from typing import Any, Callable, get_args, get_origin

from app.plugins.models import (
    ChatUIWidgetContribution,
    ContextProviderContribution,
    PluginSettingsContribution,
    PromptPatchContribution,
    RendererContribution,
    ToolContribution,
    ToolsTabContribution,
)


@dataclass
class PluginCapabilities:
    """单个插件加载后收集的所有贡献。"""

    plugin_id: str
    tools: list[ToolContribution] = field(default_factory=list)
    plugin_settings: list[PluginSettingsContribution] = field(default_factory=list)
    tools_tabs: list[ToolsTabContribution] = field(default_factory=list)
    chat_ui_widgets: list[ChatUIWidgetContribution] = field(default_factory=list)
    prompt_patches: list[PromptPatchContribution] = field(default_factory=list)
    context_providers: list[ContextProviderContribution] = field(default_factory=list)
    renderers: list[RendererContribution] = field(default_factory=list)


@dataclass
class PluginCapabilityRegistry:
    """插件初始化时使用的能力注册表。"""

    tools: list[ToolContribution] = field(default_factory=list)
    plugin_settings: list[PluginSettingsContribution] = field(default_factory=list)
    tools_tabs: list[ToolsTabContribution] = field(default_factory=list)
    chat_ui_widgets: list[ChatUIWidgetContribution] = field(default_factory=list)
    prompt_patches: list[PromptPatchContribution] = field(default_factory=list)
    context_providers: list[ContextProviderContribution] = field(default_factory=list)
    renderers: list[RendererContribution] = field(default_factory=list)

    def register_tool(self, contribution: ToolContribution) -> None:
        self.tools.append(
            replace(contribution, handler=_normalize_tool_handler(contribution.handler))
        )

    def register_plugin_settings(self, contribution: PluginSettingsContribution) -> None:
        self.plugin_settings.append(contribution)

    def register_tools_tab(self, contribution: ToolsTabContribution) -> None:
        self.tools_tabs.append(contribution)

    def register_chat_ui_widget(self, contribution: ChatUIWidgetContribution) -> None:
        self.chat_ui_widgets.append(contribution)

    def register_prompt_patch(self, contribution: PromptPatchContribution) -> None:
        self.prompt_patches.append(contribution)

    def register_context_provider(self, contribution: ContextProviderContribution) -> None:
        self.context_providers.append(contribution)

    def register_renderer(self, contribution: RendererContribution) -> None:
        self.renderers.append(contribution)

    def tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        group: str = "default",
        risk: str = "low",
        requires_confirmation: bool = False,
        capability: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """把普通函数注册为 Sakura 工具，运行时统一接收 dict 参数。"""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_tool(
                ToolContribution(
                    name=name,
                    description=description,
                    parameters=parameters or _schema_from_signature(func),
                    handler=func,
                    group=group,
                    risk=risk,
                    requires_confirmation=requires_confirmation,
                    capability=capability,
                )
            )
            return func

        return decorator


def _normalize_tool_handler(
    handler: Callable[..., Any] | None,
) -> Callable[[dict[str, Any]], Any] | None:
    if handler is None or not callable(handler):
        return None
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return lambda arguments: handler(arguments)
    if not parameters:
        return lambda _arguments: handler()
    if len(parameters) == 1:
        parameter = parameters[0]
        if (
            parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
            and (
                parameter.name in {"args", "arguments"}
                or parameter.annotation in {dict, dict[str, Any]}
            )
        ):
            return lambda arguments: handler(arguments)

    def wrapped(arguments: dict[str, Any]) -> Any:
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return handler(**arguments)
        return handler(
            **{
                parameter.name: arguments[parameter.name]
                for parameter in parameters
                if parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
                and parameter.name in arguments
            }
        )

    return wrapped


def _schema_from_signature(func: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if parameter.name in {"self", "cls"}:
            continue
        properties[parameter.name] = _schema_for_annotation(parameter.annotation)
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
    return {"type": "object", "properties": properties, "required": required}


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        if type(None) in args:
            non_null = [item for item in args if item is not type(None)]
            if non_null:
                schema = _schema_for_annotation(non_null[0])
                schema["nullable"] = True
                return schema
        if origin in {list, tuple, set}:
            item_schema = _schema_for_annotation(args[0]) if args else {}
            return {"type": "array", "items": item_schema}
        if origin is dict:
            return {"type": "object"}
    if annotation in {str, inspect.Parameter.empty}:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    return {"type": "string"}
