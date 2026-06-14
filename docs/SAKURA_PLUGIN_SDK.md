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
| `context_provider` | 注册动态上下文提供者 |
| `event.app` | 接收应用启动事件（旧 hook 机制） |
| `event.message` | 接收用户/AI 消息事件（旧 hook 机制） |
| `event.tts` | 接收 TTS 开始/结束事件（旧 hook 机制） |
| `event.character` | 接收角色加载事件（旧 hook 机制） |

> 通过 `context.events.on(...)` 订阅的新事件总线（见下文）**不需要声明权限**，
> 与上面基于权限的 `event.*` hook 机制相互独立、并存。

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
| `events` | 事件总线门面（`ScopedEventBus`），用于订阅宿主事件 |
| `services` | 宿主服务门面（`PluginServices`），用于请求 UI / TTS / Agent 行为 |
| `get_config()` | 读取插件配置（用户覆盖优先于安装目录默认） |
| `save_config(config)` | 保存插件配置（只写入用户数据目录） |
| `get_data_path(relative)` | 获取私有数据目录下的安全路径（防路径穿越） |

`PluginContext` 不提供 API Key、完整设置对象或内部服务实例（如 LLM client、
TTS manager、主窗口）。插件只能通过 `context.services` 这类受限门面与宿主交互。

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

## 事件总线订阅

除了上面基于权限的固定 hook，插件还可以通过 `context.events` 订阅一个通用事件
总线。订阅**不需要声明权限**，但插件只能订阅，不能 `emit`（事件由宿主发起）。

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
        # 卸载时取消订阅；即便不手动取消，宿主也会在卸载时清理本插件全部订阅。
        context = getattr(self, "context", None)
        if context is not None and context.events is not None:
            context.events.off(EVENT_CHAT_MESSAGE_RECEIVED, self._on_message)
            context.events.off(EVENT_TOOL_STARTED, self._on_tool_started)
```

handler 接收单个 `payload: dict` 参数。单个 handler 抛异常只会写日志，不影响
其他 handler 或宿主主流程。

已接入真实触发点的事件：

| 事件名 | 触发时机 | payload 关键字段 |
|---|---|---|
| `app.started` | 应用启动就绪 | `character_id`、`character_name` |
| `app.closing` | 应用关闭前 | `interrupted_reply` |
| `chat.message.received` | 收到用户消息 | `text`、`character_id` |
| `chat.message.sent` | AI 回复产生后 | `text`、`character_id` |
| `llm.request.started` | LLM 请求发出前 | `model` |
| `llm.request.finished` | LLM 请求成功返回 | `model` |
| `llm.request.failed` | LLM 请求失败 | `model`、`error` |
| `tool.started` | 工具开始执行 | `name`、`group`、`risk` |
| `tool.finished` | 工具执行成功 | `name` |
| `tool.failed` | 工具执行失败 | `name`、`error` |
| `tts.started` | TTS 开始朗读 | `text`、`tone`、`portrait` 等 |
| `tts.finished` | TTS 朗读结束 | 同上 |

另有一批已预留常量但尚未接入真实触发点的事件（`user.idle`、`user.returned`、
`pet.*`、`screen.*`、`agent.thinking.*`），可提前订阅，后续宿主接入后即可收到。

> 线程提示：`llm.request.*` 与 `tool.*` 可能在后台工作线程派发，handler 会在该
> 线程运行。handler 内只做轻量状态更新与日志最安全；若要操作 UI，需自行
> marshal 回 UI 线程。

## 动态上下文注入（ContextProviderContribution）

`ContextProviderContribution` 用于在**每次构建 prompt 时**动态注入一段局部上下文
（如情绪、屏幕摘要）。它与 `PromptPatchContribution` 职责不同：后者用于相对静态地
修改系统提示词与回复协议，前者用于每次请求都重新生成的动态信息。

```python
from app.plugins import PluginBase, ContextProviderContribution  # 等价：from sdk.types import ContextProviderContribution


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"

    def initialize(self, register, context):
        register.register_context_provider(
            ContextProviderContribution(
                provider_id="emotion_state",
                description="注入当前情绪状态。",
                build_context=lambda request: "当前情绪：平静\n精力：偏低",
                order=90.0,   # 越小越靠前
                enabled=True,
            )
        )
```

注册需声明 `context_provider` 权限。宿主会把各 provider 的输出按如下形式组装进
system prompt：

```text
[Plugin Context: emotion_state]
当前情绪：平静
精力：偏低

[Plugin Context: screen_awareness]
用户当前正在查看 GitHub PR 页面，可能在处理代码审查。
```

约束：`build_context` 返回字符串；单个 provider 异常或无输出会被跳过，不影响其他
provider 与主 prompt；单个 provider 输出过长会被截断。插件只贡献局部上下文，由
宿主统一组装，不要自行拼完整 prompt。

## 宿主服务门面（PluginServices）

为了让插件做有限交互又不接触内部对象，`context.services` 提供受限门面：

```python
def initialize(self, register, context):
    context.services.ui.show_bubble("我先看看～", source="my_plugin")
    context.services.tts.speak("我先看看～", interrupt=False)
    context.services.agent.request_passive_reply("用户长时间未互动")
```

| 方法 | 说明 |
|---|---|
| `services.ui.show_bubble(text, *, source=None)` | 请求宿主显示气泡提示 |
| `services.tts.speak(text, *, interrupt=False)` | 请求宿主朗读文本 |
| `services.agent.request_passive_reply(reason, context=None)` | 向宿主请求一次主动回复（由宿主决定是否执行） |

插件永远拿不到 LLM client、TTS manager、主窗口等内部实例，只能通过门面提出请求。
当前部分门面方法为最小实现（记录日志），后续会接入真实后端。

## 插件配置读写

- 安装目录默认配置：`plugins/<plugin_id>/config.json`
- 用户运行时覆盖配置：`data/plugins/<plugin_id>/config.json`

`context.get_config()` 以默认配置打底，用同名键叠加用户配置（**用户覆盖优先**）；
任一文件缺失视为 `{}`，JSON 解析失败会写日志并按 `{}` 处理。
`context.save_config(config)` **只写入用户数据目录**，不会修改安装目录默认配置。

```python
config = context.get_config()
mood = config.get("mood", "平静")
config["mood"] = "开心"
context.save_config(config)
```

## 插件私有数据目录

每个插件有独立的私有数据目录 `data/plugins/<plugin_id>/`（即 `context.data_dir`）。
用 `context.get_data_path(relative)` 获取该目录下的安全路径：拒绝绝对路径，且解析后
必须仍位于数据目录之内，否则抛 `ValueError`（防 `../../` 路径穿越）。插件只应写入
自己的数据目录。

```python
state_path = context.get_data_path("state.json")   # data/plugins/<id>/state.json
context.get_data_path("../../etc/passwd")           # 抛 ValueError
```

## 五类扩展点职责区分

| 扩展点 | 用途 |
|---|---|
| `ToolContribution` | 让 Agent 可以主动调用一个工具 |
| `PromptPatchContribution` | 修改系统提示词或回复协议（相对静态） |
| `ContextProviderContribution` | 每次请求动态注入一段上下文 |
| 事件订阅（`context.events.on`） | 监听宿主事件做状态更新或轻量反应 |
| `PluginServices` | 向宿主请求 UI / TTS / Agent 行为 |

## 接话（backchannel）插件示例

接话类插件可在 LLM 思考或工具执行期间，给用户一句轻量反馈。只需组合事件订阅与
服务门面即可（默认建议禁用，避免打扰用户）：

```python
from app.plugins import PluginBase
from app.plugins.events import EVENT_LLM_REQUEST_STARTED, EVENT_TOOL_STARTED


class BackchannelPlugin(PluginBase):
    plugin_id = "backchannel_example"

    def initialize(self, register, context):
        self.context = context
        context.events.on(EVENT_LLM_REQUEST_STARTED, self._on_llm_started)
        context.events.on(EVENT_TOOL_STARTED, self._on_tool_started)

    def _on_llm_started(self, payload):
        self.context.services.ui.show_bubble("我想想看～")
        # 如已安全接入 TTS，可改用：
        # self.context.services.tts.speak("我想想看～")

    def _on_tool_started(self, payload):
        self.context.log("工具开始", {"name": payload.get("name", "")})
```

## 把高级功能迁移为插件

迁移 Sakura 高级功能（情绪、好感、屏幕摘要、主动感知等）时的推荐做法：

1. 不是“能被 Agent 调用的动作”，而是“每次请求都要带上的状态/背景” → 用
   `ContextProviderContribution`。
2. 需要按宿主事件更新内部状态 → 用 `context.events.on(...)` 订阅。
3. 需要让 Agent 能主动执行某操作 → 用 `ToolContribution`。
4. 需要弹气泡、朗读、请求主动回复 → 用 `context.services`。
5. 配置与状态分别用 `get_config()` / `save_config()` 与 `data_dir` 持久化。

参考最小示例：`plugins/emotion_state_example/`（订阅事件 + 注入「当前桌宠状态」上下文）。

