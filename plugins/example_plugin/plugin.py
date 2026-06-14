from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.plugins import PluginBase, PluginCapabilityRegistry, PluginContext
from app.plugins import (
    ChatUIWidgetContribution,
    PromptPatchContribution,
    SettingsPanelContribution,
    ToolContribution,
    ToolsTabContribution,
)


class ExamplePlugin(PluginBase):
    """演示 Sakura 原生插件 API 常见贡献点的示例插件。"""

    plugin_id = "example_plugin"
    plugin_version = "1.0.0"

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        self.context = context

        @register.tool(
            name="sakura_example_echo",
            description="示例插件工具：回显文本，并记录调用次数。",
            group="example",
            risk="low",
            capability="example_plugin",
        )
        def echo(text: str, repeat: int = 1) -> dict[str, Any]:
            safe_repeat = max(1, min(int(repeat), 5))
            state = _update_state(context.data_dir, last_text=text)
            return {
                "message": "示例插件已生效",
                "text": text,
                "repeated": [text for _ in range(safe_repeat)],
                "call_count": state["call_count"],
                "plugin_id": context.manifest.plugin_id,
                "data_dir": str(context.data_dir),
            }

        register.register_tool(
            ToolContribution(
                name="sakura_example_status",
                description="示例插件工具：读取插件状态和最近一次回显文本。",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _arguments: _read_state(context.data_dir),
                group="example",
                risk="low",
                capability="example_plugin",
            )
        )
        register.register_tools_tab(
            ToolsTabContribution(
                tab_id="example_plugin_tools",
                title="示例插件",
                build=lambda parent=None: _build_tools_tab(context, parent),
                order=90.0,
            )
        )
        register.register_settings_panel(
            SettingsPanelContribution(
                section_id="example_plugin_settings",
                title="示例插件",
                build=lambda parent=None: _build_settings_panel(context, parent),
                order=90.0,
            )
        )
        register.register_chat_ui_widget(
            ChatUIWidgetContribution(
                widget_id="example_plugin_status",
                build=lambda parent=None: _build_chat_widget(parent),
                order=90.0,
            )
        )
        register.register_prompt_patch(
            PromptPatchContribution(
                patch_id="example_plugin_prompt",
                system_prompt_append="Sakura 示例插件已启用，可用 sakura_example_echo 和 sakura_example_status 演示插件工具调用。",
                reply_protocol_append="当用户要求验证示例插件时，优先调用 sakura_example_echo 或 sakura_example_status，并说明插件已生效。",
            )
        )


def _state_path(data_dir: Path) -> Path:
    return data_dir / "example_state.json"


def _read_state(data_dir: Path) -> dict[str, Any]:
    path = _state_path(data_dir)
    if not path.is_file():
        return {
            "message": "示例插件已生效",
            "call_count": 0,
            "last_text": "",
            "data_dir": str(data_dir),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {
        "message": "示例插件已生效",
        "call_count": int(data.get("call_count", 0)),
        "last_text": str(data.get("last_text", "")),
        "data_dir": str(data_dir),
    }


def _update_state(data_dir: Path, *, last_text: str) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    state = _read_state(data_dir)
    state["call_count"] = int(state.get("call_count", 0)) + 1
    state["last_text"] = last_text
    _state_path(data_dir).write_text(
        json.dumps(
            {"call_count": state["call_count"], "last_text": last_text},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return state


def _build_chat_widget(parent: Any = None) -> Any:
    try:
        from PySide6.QtWidgets import QLabel
    except Exception:
        return None
    label = QLabel("示例插件已启用", parent)
    label.setObjectName("examplePluginStatusLabel")
    label.setToolTip("来自 Sakura 示例插件的聊天区控件")
    return label


def _build_tools_tab(context: PluginContext, parent: Any = None) -> Any:
    try:
        from PySide6.QtWidgets import QLabel
    except Exception:
        return None
    state = _read_state(context.data_dir)
    label = QLabel(
        f"示例插件工具页已加载。工具调用次数：{state['call_count']}，最近文本：{state['last_text'] or '无'}",
        parent,
    )
    label.setWordWrap(True)
    return label


def _build_settings_panel(context: PluginContext, parent: Any = None) -> Any:
    try:
        from PySide6.QtWidgets import QLabel
    except Exception:
        return None
    label = QLabel(
        f"示例插件设置面板已加载。\n插件目录：{context.plugin_root}\n数据目录：{context.data_dir}",
        parent,
    )
    label.setWordWrap(True)
    return label
