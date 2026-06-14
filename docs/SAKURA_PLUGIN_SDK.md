# Sakura 插件 SDK

Sakura 插件是在宿主进程内运行的 Python 扩展。插件不是安全沙箱，可以访问文件系统、网络和宿主进程环境。只安装可信来源的插件。

## 插件结构

推荐结构：

```text
plugins/
  my_plugin/
    __init__.py
    plugin.yaml
    plugin.py
```

`plugin.yaml`：

```yaml
api_version: 1
id: my_plugin
name: My Plugin
version: 1.0.0
entry: plugin:MyPlugin
enabled: true
priority: 100
permissions:
  - tool
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `api_version` | 是 | 当前为 `1` |
| `id` | 是 | 插件唯一标识，建议使用小写字母、数字和下划线 |
| `name` | 否 | 设置页和日志中显示的名称 |
| `version` | 否 | 插件版本 |
| `entry` | 是 | 入口类，格式为 `module:ClassName`，相对插件目录 |
| `enabled` | 否 | 默认 `true` |
| `priority` | 否 | 加载优先级，数值越大越先加载 |
| `required` | 否 | 必需插件加载失败时停止继续加载后续插件 |
| `permissions` | 是 | 插件权限声明，缺失或未知权限会导致加载失败 |

固定权限：

| 权限 | 说明 |
|---|---|
| `tool` | 注册 Agent 工具 |
| `tools_tab` | 注册“工具”页扩展 |
| `settings_panel` | 注册“插件”页设置面板 |
| `chat_ui` | 注册聊天输入区控件 |
| `prompt_patch` | 注册提示词补丁 |
| `event.app` | 接收应用启动事件 |
| `event.message` | 接收用户/AI 消息事件 |
| `event.tts` | 接收 TTS 开始/结束事件 |
| `event.character` | 接收角色加载事件 |

`data/config/plugins.yaml` 只负责启停和优先级覆盖：

```yaml
- id: my_plugin
  enabled: true
  priority: 100
```

## 最小插件

```python
from app.plugins import PluginBase, PluginCapabilityRegistry, PluginContext
from app.plugins import ToolContribution


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"
    plugin_version = "1.0.0"

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        register.register_tool(
            ToolContribution(
                name="my_plugin_echo",
                description="回显文本。",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=lambda args: {"text": args["text"]},
                group="default",
                risk="low",
            )
        )

    def shutdown(self) -> None:
        pass
```

`PluginContext` 提供：

| 属性/方法 | 说明 |
|---|---|
| `base_dir` | Sakura 项目根目录 |
| `plugin_root` | 当前插件目录 |
| `data_dir` | 当前插件私有数据目录：`data/plugins/<plugin_id>/` |
| `manifest` | 插件清单视图 |
| `log(message, data=None)` | 写入 Sakura 调试日志 |

`PluginContext` 不提供 API Key、完整设置对象或内部服务实例。

## 工具注册

工具名必须符合 OpenAI function name 约束：`A-Z`、`a-z`、`0-9`、`_`、`-`，长度 1-64。工具名不能和内置工具、MCP 工具或其他插件工具重复。

也可以使用无全局状态的装饰器：

```python
class MyPlugin(PluginBase):
    plugin_id = "my_plugin"

    def initialize(self, register, context):
        @register.tool(
            name="my_plugin_add",
            description="计算两个整数之和。",
            group="default",
            risk="low",
        )
        def add(a: int, b: int) -> dict[str, int]:
            return {"result": a + b}
```

装饰器会根据函数签名生成 JSON Schema。需要精确 schema 时，传入 `parameters`。

## 贡献点

| 方法 | 类型 | 接入位置 |
|---|---|---|
| `register_tool()` | `ToolContribution` | Agent 可调用工具 |
| `register_tools_tab()` | `ToolsTabContribution` | 设置窗口的“工具”页 |
| `register_settings_panel()` | `SettingsPanelContribution` | 设置窗口的“插件”页 |
| `register_chat_ui_widget()` | `ChatUIWidgetContribution` | 主窗口输入栏 |
| `register_prompt_patch()` | `PromptPatchContribution` | Agent 系统提示词和回复协议 |

设置页和聊天 UI 的 `build(parent)` 应返回 PySide6 `QWidget`。构建失败时宿主会显示降级文本，不会阻止 Sakura 启动。

`PromptPatchContribution`：

```python
from app.plugins import PromptPatchContribution

register.register_prompt_patch(
    PromptPatchContribution(
        patch_id="my_plugin_prompt",
        system_prompt_append="插件提供的角色补充设定。",
        reply_protocol_append="插件要求的回复约束。",
    )
)
```

## 事件 Hook

插件可以按权限接收宿主事件。未声明对应 `event.*` 权限时，宿主不会调用 hook；hook 抛错只会写日志，不影响主流程。

```python
from app.plugins import PluginBase


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"

    def initialize(self, register, context):
        self.context = context

    def on_user_message(self, event):
        self.context.log("收到用户消息", {"text": event.payload.get("text", "")})
```

可实现的 hook：

| 方法 | 权限 |
|---|---|
| `on_app_start(event)` | `event.app` |
| `on_user_message(event)` | `event.message` |
| `on_ai_message(event)` | `event.message` |
| `on_tts_start(event)` | `event.tts` |
| `on_tts_end(event)` | `event.tts` |
| `on_character_loaded(event)` | `event.character` |

