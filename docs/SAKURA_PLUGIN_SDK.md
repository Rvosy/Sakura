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
| `renderer` | 注册角色渲染后端（接管角色显示） |
| `audio_input` | 访问麦克风/录音输入设备 |
| `network` | 访问网络（例如调用远端服务或下载资源） |
| `model_download` | 下载模型、权重或大体积资源 |
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

## 插件 API 版本与兼容策略

`api_version` 声明插件面向的契约版本，当前为 `1`。宿主维护一个**受支持版本集合**
（见 `app.plugins.SUPPORTED_API_VERSIONS`，当前为 `{1}`）；`api_version` 不在集合内的
插件会加载失败。

向前兼容承诺——在同一 `api_version` 内，宿主只做**加法式演进**，已发布插件无需改动即可
继续工作：

- 新增贡献点、新增权限、新增事件常量与触发点；
- 给贡献对象（`ToolContribution` 等）或 `RendererCreateContext` 新增**带默认值**的字段；
- 给 `PluginContext` / `PluginServices` 新增方法；
- 把当前为最小实现（仅记日志）的服务门面接入真实后端（签名不变）。

需要升级 `api_version` 的**破坏性变更**（例如：删除或重命名已有字段、修改方法签名或
语义、移除贡献点或权限）发生时，宿主会在过渡期内**同时支持新旧版本**
（如 `SUPPORTED_API_VERSIONS = {1, 2}`），让存量 v1 插件不被一次性破坏。

插件作者建议：固定声明 `api_version`；读取事件 payload 用 `.get()` 容错；只依赖本文档
列出的公开接口，不要触碰宿主内部对象与未文档化的实现细节。

## .plugin 插件包

Sakura 插件包是 ZIP 压缩包，文件后缀固定为 `.plugin`。包内结构是插件目录的
**内容本身**，根目录必须包含 `plugin.yaml`，入口文件路径必须与 `entry` 对应：

```text
my_plugin.plugin
  plugin.yaml
  plugin.py
  assets/
    icon.png
```

导出规则：

- 从 `plugins/<id>/` 打包目录内容，归档内不包含最外层 `<id>/` 目录。
- 默认排除 `data/`、`models/`、`model_cache/`、`cache/`、`temp/`、`tmp/`、
  `runtime/`、`__pycache__/` 等运行时数据、模型缓存、临时文件和 Python 字节码。
- 插件私有用户数据位于 `data/plugins/<id>/`，不会随插件导出。
- 导出目标不能放在插件目录内部，避免把正在生成的包再次打进包里。

导入规则：

- 只接受 `.plugin` 后缀。
- 导入前校验 `plugin.yaml` 的 `api_version`、`id`、`entry`、`permissions`；
  API 版本不受支持、权限未知、入口文件缺失都会失败。
- ZIP 成员必须是安全相对路径；绝对路径、Windows 盘符路径、`..` 路径穿越、
  空路径片段、符号链接都会被拒绝。
- 目标目录固定为 `plugins/<id>/`。如果同 ID 插件已存在，导入失败，不会静默覆盖。
- 导入过程先解压到临时目录，全部校验通过后再移动到最终目录，避免部分写入覆盖宿主文件。

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
| `register_context_provider()` | `ContextProviderContribution` | 每次构建 prompt 时动态注入上下文 |
| `register_renderer()` | `RendererContribution` | 角色渲染后端（接管角色显示） |

设置页和聊天 UI 的 `build(parent)` 应返回 PySide6 `QWidget`。构建失败时宿主会显示降级文本，不会阻止 Sakura 启动。

`SettingsPanelContribution` 示例：

```python
from app.plugins import PluginBase, SettingsPanelContribution


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"

    def initialize(self, register, context):
        self.context = context
        register.register_settings_panel(
            SettingsPanelContribution(
                section_id="my_plugin_settings",
                title="My Plugin",
                build=lambda parent=None: self._build_settings(parent),
                order=100.0,
            )
        )

    def _build_settings(self, parent=None):
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel(f"数据目录：{self.context.data_dir}", widget))
        return widget
```

注册设置面板需声明 `settings_panel` 权限。设置控件应通过 `context.get_config()` /
`context.save_config()` 读写插件私有配置，不要修改安装目录下的默认配置。

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

## shutdown 资源释放

插件卸载或 Sakura 关闭时会调用 `shutdown()`。插件应在这里主动释放自己创建的资源：

- 停止录音、屏幕采集、文件监控等后台任务；
- 取消下载、ASR/推理、网络请求等仍在运行的 worker；
- 关闭线程池、进程、socket、文件句柄；
- 释放麦克风、模型、GPU/CPU 推理会话等外部资源；
- 取消通过 `context.events.on(...)` 注册的事件订阅。即使插件忘记取消，宿主也会按
  plugin_id 清理事件总线订阅，但插件自己的资源仍应自行关闭。

`shutdown()` 不应长时间阻塞 UI 线程。耗时清理应尽量通过可取消任务提前停止，
并在短超时内完成。

## 动态上下文注入（ContextProviderContribution）

`ContextProviderContribution` 用于在**每次构建 prompt 时**动态注入一段局部上下文
（如情绪、屏幕摘要）。它与 `PromptPatchContribution` 职责不同：后者用于相对静态地
修改系统提示词与回复协议，前者用于每次请求都重新生成的动态信息。

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

## 角色渲染后端（RendererContribution）

渲染器贡献点让插件提供一个新的**角色显示后端**（如 MMD / Live2D / VRM），接管桌宠
角色的显示，而宿主不硬编码任何具体后端。注册需声明 `renderer` 权限。

职责划分：渲染后端的**生命周期、降级与事件接入由宿主的 `RendererManager` 负责**，
插件只贡献一个「工厂 + 渲染器实现」。这样保证某个后端在当前环境不可用或初始化失败
时，宿主能自动回退到默认 PNG 立绘，绝不因插件渲染器异常而崩溃。

### 实现渲染器

渲染器必须继承 `app.renderers.CharacterRenderer`。基类所有方法默认是空操作（no-op），
子类只覆盖自己支持的能力即可；未覆盖的方法静默忽略，不抛异常。

```python
from typing import Any

from app.plugins import PluginBase, RendererContribution, RendererCreateContext
from app.renderers import CharacterRenderer


class MyRenderer(CharacterRenderer):
    renderer_name = "my_backend"
    # 接管角色主体显示时置 True，宿主会隐藏默认 PNG 立绘。
    replaces_default_portrait = True

    def __init__(self, ctx: RendererCreateContext) -> None:
        self._ctx = ctx

    def is_available(self) -> bool:
        # 探测当前环境是否满足后端依赖（缺依赖返回 False，宿主自动降级）。
        return True

    def initialize(self, app_context: dict[str, Any] | None = None) -> None:
        # 创建窗口、加载资源等；失败请抛异常，由宿主捕获并回退 default。
        ...

    def load_character(self, character_config: dict[str, Any]) -> None:
        ...

    def show(self) -> None: ...
    def hide(self) -> None: ...
    def close(self) -> None: ...
    def handle_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        # 接收宿主转发的事件（tts.started / llm.request.started / pet.* 等）。
        ...


class MyRendererPlugin(PluginBase):
    plugin_id = "my_renderer"

    def initialize(self, register, context):
        register.register_renderer(
            RendererContribution(
                renderer_type="my_backend",   # 与角色包 renderer 配置的 type 对应
                display_name="My Backend",
                create=lambda ctx: MyRenderer(ctx),
                priority=100.0,               # 同 type 多贡献时取 priority 最大者
            )
        )
```

`CharacterRenderer` 可覆盖的能力方法（均为可选）：`initialize` / `load_character` /
`show` / `hide` / `close` / `set_position` / `set_geometry` / `stack_below` /
`set_scale` / `play_motion` / `stop_motion` / `set_expression` / `set_lip_sync` /
`look_at` / `handle_event` / `is_available`。

### 工厂入参 RendererCreateContext

`create(ctx)` 收到的 `ctx` 字段（宿主只传稳定、受限的信息，具体模型路径与动作表由
插件自行从 `package_dir` 和 `renderer_config` 解析）：

| 字段 | 说明 |
|---|---|
| `character_id` | 当前角色 ID |
| `character_name` | 当前角色显示名 |
| `package_dir` | 当前角色包目录（解析模型/动作资源的根） |
| `renderer_config` | 角色包中该渲染器的配置字典 |
| `owner_window` | 宿主主窗口（用于窗口层级协调，可为 None） |
| `event_bus` | 事件总线（独立窗口后端可用于自行订阅，可为 None） |

### 角色如何选择后端

由**角色包的 renderer 配置**决定，而非全局开关。配置 `type` 命中某个插件贡献的
`renderer_type` 时启用该后端；`fallback` 指定回退后端（当前仅支持 `default`）：

```yaml
# 角色包 renderer 配置（节选）
renderer:
  type: my_backend
  fallback: default
```

降级与隔离规则：

- `type` 缺省或为 `default` → 使用内置默认 PNG 立绘渲染器。
- `type` 未匹配到任何贡献，或工厂抛异常，或 `is_available()` 返回 False，或
  `initialize()` 失败 → 自动回退到 `default`，不影响宿主启动。
- 渲染器后续每次被调用（show/play_motion/handle_event 等）的异常都被宿主隔离，
  只写日志，不影响主流程。
- 同一 `renderer_type` 不允许多个插件重复贡献，加载阶段即拦截。

## 宿主服务门面（PluginServices）

为了让插件做有限交互又不接触内部对象，`context.services` 提供受限门面：

```python
def initialize(self, register, context):
    context.services.ui.show_bubble("我先看看～", source="my_plugin")
    context.services.tts.speak("我先看看～", interrupt=False)
    context.services.agent.request_passive_reply("用户长时间未互动")
    context.services.input.set_input_text("把这段文本填进输入框")
    context.services.input.append_input_text("追加到末尾")
    context.services.input.insert_input_text("插到当前光标")
```

| 方法 | 说明 |
|---|---|
| `services.ui.show_bubble(text, *, source=None)` | 请求宿主显示气泡提示 |
| `services.tts.speak(text, *, interrupt=False)` | 请求宿主朗读文本 |
| `services.agent.request_passive_reply(reason, context=None)` | 向宿主请求一次主动回复（由宿主决定是否执行） |
| `services.input.set_input_text(text)` | 把文本**填入**聊天输入框（替换当前内容，不发送），由用户确认/编辑后再发送 |
| `services.input.append_input_text(text)` | 把文本追加到聊天输入框末尾（不发送） |
| `services.input.insert_input_text(text, position=None)` | 把文本插入到指定位置；`position=None` 表示当前光标（不发送） |

插件永远拿不到 LLM client、TTS manager、主窗口等内部实例，只能通过门面提出请求。

`services.input.*` 已接入真实后端，可在后台线程调用（宿主会 marshal 回 UI 线程）——
这正是语音输入（ASR）插件的典型用法：识别完成后把结果填进输入框。其余门面方法
（`ui` / `tts` / `agent`）当前为最小实现（记录日志），后续接入真实后端，签名保持不变。

## 语音输入（ASR）插件示例

在输入栏添加一个语音按钮，识别完成后把文本**填入输入框**（不自动发送，让用户确认/
编辑后再发）。组合 `chat_ui_widget`（按钮）与 `services.input.set_input_text`（回灌文本）
即可。`plugin.yaml` 至少需声明 `chat_ui`、`audio_input` 权限；如需在线下载 ASR 模型，
还需声明 `network` 与 `model_download`。

```python
from app.plugins import PluginBase, ChatUIWidgetContribution


class VoiceInputPlugin(PluginBase):
    plugin_id = "voice_input"

    def initialize(self, register, context):
        self.context = context
        register.register_chat_ui_widget(
            ChatUIWidgetContribution(
                widget_id="voice_input_button",
                build=self._build_button,
                order=50.0,   # order 越小越靠输入框（落在输入框右侧、发送键左边那一簇）
            )
        )

    def _build_button(self, parent):
        from PySide6.QtWidgets import QToolButton

        button = QToolButton(parent)
        button.setText("🎤")
        button.clicked.connect(self._on_clicked)
        return button

    def _on_clicked(self):
        # 启动录音 + ASR（插件自带，可在后台线程进行）。识别完成后回灌输入框：
        text = self._run_asr()                       # 你的 ASR 实现，返回识别文本
        self.context.services.input.set_input_text(text)
```

`set_input_text` 可安全地在后台线程调用（宿主自动 marshal 回 UI 线程），因此 ASR 的
异步回调里直接调用即可。文本填入后会替换输入框当前内容并聚焦，用户回车或点发送即可。

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

