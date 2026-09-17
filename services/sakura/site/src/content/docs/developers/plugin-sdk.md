---
title: 插件 SDK
description: Sakura 插件结构、权限、工具注册、事件订阅和扩展点。
---

# 插件 SDK

Sakura 插件是在宿主进程内运行的 Python 扩展。插件不是安全沙箱，可以访问文件系统、网络和宿主进程环境，因此只应安装可信来源的插件。

## 插件结构

推荐结构：

```text
plugins/
  my_plugin/
    __init__.py
    plugin.yaml
    plugin.py
```

`plugin.yaml` 示例：

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

| 字段 | 必填 | 说明 |
|---|---:|---|
| `api_version` | 是 | 当前为 `1` |
| `id` | 是 | 插件唯一标识，建议小写字母、数字和下划线 |
| `name` | 否 | 设置页和日志中显示的名称 |
| `version` | 否 | 插件版本 |
| `entry` | 是 | 入口类，格式为 `module:ClassName`，相对插件目录 |
| `enabled` | 否 | 默认 `true` |
| `priority` | 否 | 加载优先级，数值越大越先加载 |
| `required` | 否 | 必需插件加载失败时停止继续加载 |
| `permissions` | 是 | 插件权限声明，缺失或未知权限会导致加载失败 |

## 固定权限

| 权限 | 说明 |
|---|---|
| `tool` | 注册 Agent 工具 |
| `tools_tab` | 注册"工具"页扩展 |
| `settings_panel` | 注册"插件"页设置面板 |
| `chat_ui` | 注册聊天输入区控件 |
| `prompt_patch` | 注册提示词补丁 |
| `context_provider` | 注册动态上下文提供者 |
| `renderer` | 注册角色渲染后端 |
| `event.app` | 接收应用启动事件 |
| `event.message` | 接收用户或 AI 消息事件 |
| `event.tts` | 接收 TTS 开始或结束事件 |
| `event.character` | 接收角色加载事件 |

通过 `context.events.on(...)` 订阅的新事件总线不需要声明权限，与基于权限的 `event.*` hook 机制并存。

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

| 属性或方法 | 说明 |
|---|---|
| `base_dir` | Sakura 项目根目录 |
| `plugin_root` | 当前插件目录 |
| `data_dir` | 当前插件私有数据目录 |
| `manifest` | 插件清单视图 |
| `log(message, data=None)` | 写入 Sakura 调试日志 |
| `events` | 事件总线门面 |
| `services` | 宿主服务门面 |
| `get_config()` | 读取插件配置 |
| `save_config(config)` | 保存插件配置 |
| `get_data_path(relative)` | 获取私有数据目录下的安全路径 |

插件拿不到 LLM client、TTS manager、主窗口等内部实例，只能通过受限门面与宿主交互。

## 工具注册

工具名必须符合 OpenAI function name 约束：`A-Z`、`a-z`、`0-9`、`_`、`-`，长度 1 到 64。工具名不能和内置工具、MCP 工具或其他插件工具重复。

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

装饰器根据函数签名生成 JSON Schema。需要精确 schema 时，传入 `parameters`。

## 贡献点

| 方法 | 类型 | 接入位置 |
|---|---|---|
| `register_tool()` | `ToolContribution` | Agent 可调用工具 |
| `register_tools_tab()` | `ToolsTabContribution` | 设置窗口的"工具"页 |
| `register_settings_panel()` | `SettingsPanelContribution` | 设置窗口的"插件"页 |
| `register_chat_ui_widget()` | `ChatUIWidgetContribution` | 主窗口输入栏 |
| `register_prompt_patch()` | `PromptPatchContribution` | Agent 系统提示词和回复协议 |
| `register_context_provider()` | `ContextProviderContribution` | 每次构建 prompt 时动态注入上下文 |
| `register_renderer()` | `RendererContribution` | 角色渲染后端 |

## 事件总线订阅

```python
from app.plugins import PluginBase
from app.plugins.events import EVENT_CHAT_MESSAGE_RECEIVED, EVENT_TOOL_STARTED


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"

    def initialize(self, register, context):
        self.context = context
        context.events.on(EVENT_CHAT_MESSAGE_RECEIVED, self._on_message)
        context.events.on(EVENT_TOOL_STARTED, self._on_tool_started)

    def _on_message(self, payload):
        self.context.log("收到消息", {"text": payload.get("text", "")})

    def _on_tool_started(self, payload):
        self.context.log("工具开始", {"name": payload.get("name", "")})

    def shutdown(self):
        context = getattr(self, "context", None)
        if context is not None and context.events is not None:
            context.events.off(EVENT_CHAT_MESSAGE_RECEIVED, self._on_message)
            context.events.off(EVENT_TOOL_STARTED, self._on_tool_started)
```

已接入真实触发点的事件：

| 事件名 | 触发时机 |
|---|---|
| `app.started` | 应用启动就绪 |
| `app.closing` | 应用关闭前 |
| `chat.message.received` | 收到用户消息 |
| `chat.message.sent` | AI 回复产生后 |
| `llm.request.started` | LLM 请求发出前 |
| `llm.request.finished` | LLM 请求成功返回 |
| `llm.request.failed` | LLM 请求失败 |
| `tool.started` | 工具开始执行 |
| `tool.finished` | 工具执行成功 |
| `tool.failed` | 工具执行失败 |
| `tts.started` | TTS 开始朗读 |
| `tts.finished` | TTS 朗读结束 |

`llm.request.*` 与 `tool.*` 可能在后台工作线程派发。handler 内只做轻量状态更新与日志最安全；若要操作 UI，需自行 marshal 回 UI 线程。

## 动态上下文注入

`ContextProviderContribution` 在每次构建 prompt 时注入局部上下文，例如情绪、屏幕摘要或插件状态。

```python
from app.plugins import PluginBase, ContextProviderContribution


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"

    def initialize(self, register, context):
        register.register_context_provider(
            ContextProviderContribution(
                provider_id="emotion_state",
                description="注入当前情绪状态。",
                build_context=lambda request: "当前情绪：平静\n精力：偏低",
                order=90.0,
                enabled=True,
            )
        )
```

注册需声明 `context_provider` 权限。单个 provider 异常或无输出会被跳过，不影响其他 provider 和主 prompt。

## 宿主服务门面

```python
def initialize(self, register, context):
    context.services.ui.show_bubble("我先看看～", source="my_plugin")
    context.services.tts.speak("我先看看～", interrupt=False)
    context.services.agent.request_passive_reply("用户长时间未互动")
    context.services.input.set_input_text("把这段文本填进输入框")
```

| 方法 | 说明 |
|---|---|
| `services.ui.show_bubble(...)` | 请求宿主显示气泡提示 |
| `services.tts.speak(...)` | 请求宿主朗读文本 |
| `services.agent.request_passive_reply(...)` | 向宿主请求一次主动回复 |
| `services.input.set_input_text(...)` | 把文本填入聊天输入框，由用户确认后发送 |

`set_input_text` 可安全地在后台线程调用，适合语音输入插件在识别完成后回填输入框。
